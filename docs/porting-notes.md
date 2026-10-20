# Porting notes

What it took to move this box from L4T 32.4.3 to R35.6.5. The first section
applies to other camera ports on R35 too. The rest is specific to the
LI-XNX-BOX-GMSL2.

## Pitfalls for other camera ports

- **PCA954x idle state.** NVIDIA's R35 mux driver still treats `idle_state` as
  the R32 per-channel bitmask. Adding child bus 0 turns `MUX_IDLE_DISCONNECT`
  (-2) into `MUX_IDLE_AS_IS` (-1), so the channels are never deselected and the
  camera pairs are no longer isolated. Fixed by
  `patches/r35.6.5-pca954x-idle-state.patch`. R35 also reads
  `i2c-mux-idle-disconnect` from the switch node, not
  `i2c-mux,deselect-on-exit` from each child bus.
- **`skip_mux_detect` is ignored.** R35 always talks to the mux at probe. Here
  the mux must register its child buses before the bridge driver can switch on
  camera power. Fixed by `patches/r35.6.5-pca954x-skip-detect.patch`.
- **`serdes_pix_clk_hz` changed meaning.** R32 passed the value straight to the
  CSI clock. R35's `sensor_common.c` treats it as a pixel rate and converts it
  to a lane rate. For 1.5 Gbit/s per lane, two lanes and 12-bit pixels the R35
  value is 250 MHz (750 MHz D-PHY DDR clock). Copying the R32 value of 1.5 GHz
  requests 4.5 GHz.
- **The capture graph moved.** R35 expects the VI endpoints under
  `/tegra-capture-vi`; the VI node under `host1x` no longer owns them. Disable
  the devkit's ribbon-camera nodes (`cam_i2cmux`), or the IMX219 driver
  probes against endpoints that are gone.
- **A device tree exported with `dtc -I fs` loses node order.** NVIDIA's VI
  graph walker pairs endpoints with channels in sibling order, while its port
  parser selects them by `reg`. `port-device-tree.py` sorts the nodes so both
  agree.
- **Keep the sensor's I2C name short.** The VI driver names each device
  `vi-output, NAME BUS-ADDR` in the 32-byte V4L2 card field, and Argus uses
  that string to find the sensor. With a longer name the bus and address are
  cut off and Argus finds no cameras. The sensor here is `li-imx185`.
- **Tegra I2C does not clear a stuck bus after a timeout.** R35 runs the
  nine-clock bus clear only after arbitration loss. The bridge driver calls
  `i2c_recover_bus()` itself.
- **`nvarguscamerasrc` can exit successfully with zero frames.** Count buffers
  and check timestamps (`test-argus.py`) instead of trusting the exit code.

## Hardware

**I2C.** Controller `3180000` (adapter 2) connects to a TCA9548 switch at 0x77.
Channels 0–2 are adapters 30–32. Each channel has one MAX9296A deserializer at
0x48 and two MAX9295 serializers, which both start at 0x62. The setup moves the
first one to 0x60 while it configures them one at a time, then moves it back.
The serializers translate the sensor addresses, so the two IMX185s appear at
0x28 and 0x2a. The bus runs at 400 kHz.

**The mux reads back 0xff**, also when everything works, although TI
documents reading back the channel mask. Writes select channels correctly:
each channel's deserializer keeps its own register contents. Do not use the
readback as a health check on this box.

**Cameras.** Each camera has its own two-lane CSI port: RAW12 RGGB,
1920×1080 at 30 fps, 74.25 MHz pixel clock, 2200 pixels per line. The serializer
generates the sensor's 27 MHz clock and drives its reset, so the NVIDIA sensor
driver's reset (XCLR) toggling is removed. MAX9296A register 0x0332 enables the
CSI output: bit 0x20 for the first camera of a pair, 0x40 for the second.

**Power and GPIOs.**

- AON GPIO 16 (PCC.00) switches camera power. Switching it off and on
  restarts the cameras, but the deserializers keep their registers. Neither
  this pin nor the camera control outputs below reset them. The devkit tree
  has a `w-disable2` GPIO hog on this pin; the port removes the hog and hands
  the pin to the bridge driver.
- Four camera control outputs (PP.04, PP.05, PQ.03 and PR.00) are held low, as
  in the original tree. The devkit tree lacks PQ.03 and PR.00.
- The microSD slot is on SDMMC3, with card detect on GPIO 130 and its
  regulator enable on GPIO 129 of the main GPIO controller. The devkit tree
  disables that controller and gives the regulator of the same name another
  GPIO; the port restores the original wiring.

**USB.** The original image forces the micro-USB port (USB2-0) into host mode,
with its 5 V supply also on GPIO 129. The ported tree keeps NVIDIA's OTG setup
instead, so micro-USB works as the device and recovery port; use the USB-C
port for a keyboard or storage. `port-device-tree.py --usb0-host` restores the
original host mode. Do not use it with micro-USB connected to a host. All
installs during bring-up went through OTA updates, never USB recovery mode.

**Link setup.** The original driver writes 59 registers per pair. It waits
500 ms after switching on camera power and 300 ms after each link select or
address change. The sensor mode is 159 writes. The original device tree uses
Leopard's `nvidia,max929x` driver; the separate MAX9295/MAX9296 drivers are not
on its camera path.

## Startup after a reboot

The deserializers keep their registers through a reboot. The cameras restart,
and their serializers come back unconfigured.

Leopard's setup leaves each deserializer with both GMSL links enabled: it
writes `CTRL0` (0x0010) = 0x23, which reads back as 0x03. With both links
enabled and the serializers unconfigured, the deserializer answers its own I2C
address unreliably. It NAKs, returns corrupted data, stretches the clock up to
its 32 ms remote timeout and sometimes holds the clock, which blocks the whole
camera bus. It stays like that for minutes, and switching camera power off
does not end it. Writing 0x03 to a healthy deserializer whose serializers are
unconfigured causes this at once.

The likely reason: for two cameras on one deserializer, ADI's procedure gives
each serializer its own I2C address and packet source IDs before enabling both
links ([GMSL2 General User Guide](https://www.analog.com/media/en/technical-documentation/user-guides/gmsl2-general-user-guide.pdf),
section 16.2.3.1.4.1). Restarted serializers have lost both. Leopard's setup
follows the procedure, except that it moves the first serializer back to 0x62
at the end; that works because nothing addresses the serializers afterwards.

With link A selected alone (`CTRL0` = 0x01), the deserializer keeps working
while the cameras restart. The bridge driver therefore:

1. selects link A at shutdown, when it is unloaded and when the setup fails,
   so a normal reboot starts in a working state;
2. at probe, writes `CTRL0` = 0x01, waits 20 ms, reads it back and checks the
   chip ID (0x94) three times, and repeats up to 100 times until all of them
   are right. It clears the bus after a timeout. In the bad state a write can
   land without an acknowledgement and vice versa, so only the readback
   counts. Reading back without the wait needed many more attempts;
3. if that fails, fully resets the deserializer and selects link A again, up
   to 60 times (see below);
4. then runs Leopard's setup unchanged. Its first write, 0x21, adds the link
   reset (`RESET_ONESHOT`) that the datasheet asks for after a link change.

`leopard-gmsl init BUS --apply` takes steps 2 to 4 too, but cannot clear a
stuck bus.

Channel 0 owns the shared camera power. If its own pair fails, it stays bound
and keeps the power on, so pairs 1 and 2 still start.

Counting only pairs that still answered: with link A selected before a reboot
or module reset, and after a cold start, the pairs started on the first
attempt every time. After hard reboots that skip the shutdown step, link A
selection alone failed on 14 of 23 pair starts. The full reset brought pairs
back after 1 to 24 resets. With an earlier limit of 20 resets one pair failed;
reloading the driver then took 11.

The original 32.4.3 driver writes the setup once and ignores failures. Its
recorded reboot shows failed writes on two of the three pairs.

### When a deserializer stops answering

Keep other writes to a deserializer in the bad state to a minimum. During
testing, one pair stopped answering at any address after many such writes;
corrupted registers are suspected but not proven. None of these brought it back:
module resets through the PMIC, also with slower power sequencing, camera
power off for 30 seconds, SC7 sleep, the four camera control outputs and
link B. Disconnecting power brought it back.

A full reset (`RESET_ALL`, `CTRL0` bit 7) needs a deserializer that still
answers. It turns automatic link selection back on, which may choose link B,
so select link A after the reset and trust that readback. Checking for the
reset value of `CTRL0` (0x11) instead failed 60 times in a row on a pair that
link A selection reached at once afterwards. On a working bridge, writing 0x81
(reset, link A) restored a test value in 0x0333 to its default.

Writing 0x12 to REG1 (0x0001) to cut the remote control channel made a working
pair unreachable, and it took repeated writes to bring it back. Do not use it
for recovery.

Resetting a deserializer without disconnecting power would need control of its
PWDNB pin (pin 1, active low). The device tree shows no such signal.

## How the configuration was recovered

The reference was the running 32.4.3 system, not the product documentation.

1. Export the live device tree. A DTB found on disk did not contain the
   six-camera topology.
2. Trace the I2C traffic with ftrace while rebinding the original driver and
   streaming each camera (`capture-baseline.sh`).
3. Locate the register tables in the original kernel `Image` with `kallsyms`
   and Ghidra (`LeopardFunctions.java` decompiles only the camera functions),
   then extract them into `driver/profile.json` (`extract-profile.py`).
4. Check the tables against the traces (`validate-trace.py`,
   `test-evidence.py`): all 59 link writes match on each of the three pairs,
   and all 159 mode writes match on every sensor.

The new drivers contain no decompiled code. The sensor driver is NVIDIA's R35.6.5
IMX185 driver, changed by `prepare-sensor.py`.

## Datasheets

- [TCA9548A](https://www.ti.com/lit/ds/symlink/tca9548a.pdf) (TI)
- [MAX9296A](https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/7130/175_MAX9296AGTMVT11.pdf)
  (Maxim, hosted by Digi-Key)
- [GMSL2 General User Guide](https://www.analog.com/media/en/technical-documentation/user-guides/gmsl2-general-user-guide.pdf)
  (ADI)
- [LI-XNX-BOX-GMSL2 product datasheet](https://leopardimaging.com/wp-content/uploads/2025/09/LI-XNX-BOX-GMSL2_datasheet.pdf)
  (Leopard). It has no schematic or power wiring.
- MAX9295 from Analog Devices, IMX185LQJ-C from Sony. The exact MAX9295
  variant on the camera modules is unknown; do not assume the MAX9295D
  register map.
