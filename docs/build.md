# Build and install

Everything is built on an x86 Linux host and installed by upgrading the box
over the air: 32.4.3 → 32.7.3 → 35.6.5.

## Inputs and directory layout

Work in this checkout. These directories are ignored by Git:

- `r35/`: NVIDIA's R35.6.5 public kernel sources, with the kernel at
  `r35/kernel/kernel-5.10` and NVIDIA's tree at `r35/kernel/nvidia`.
- `toolchain/`: the Bootlin GCC 9.3 AArch64 toolchain linked from the
  [R35.6.5 release page](https://developer.nvidia.com/embedded/jetson-linux-r3565).
- `bsp35/Linux_for_Tegra/`: the R35.6.5 BSP and sample rootfs, with NVIDIA's
  binaries applied (`apply_binaries.sh`).
- `inputs/live-r32.4.3.dtb` and `inputs/camera_overrides.isp`: the two files
  saved from the box before upgrading (see the [README](../README.md#what-you-need)).
- `stock/tegra194-p3668-0001-p3509-0000.dtb`: the untouched R35.6.5 Xavier NX
  eMMC DTB from the BSP.

Host packages: a C toolchain, `make`, `patch`, `bc`, `bison`, `flex`, OpenSSL
and libelf headers, device-tree tools, Python 3 and `python3-libfdt`. NVIDIA's
release guide lists what its BSP and OTA tools need in addition.

## Kernel and camera modules

Apply the two patches to a clean source tree, build the kernel with the tested
configuration, then the camera modules and the userspace tool:

```sh
patch -d r35/kernel/kernel-5.10 -p1 < patches/r35.6.5-pca954x-idle-state.patch
patch -d r35/kernel/kernel-5.10 -p1 < patches/r35.6.5-pca954x-skip-detect.patch
mkdir -p out35
cp config/tested-r35.6.5.config out35/.config
make -C r35/kernel/kernel-5.10 O="$PWD/out35" ARCH=arm64 \
  CROSS_COMPILE="$PWD/toolchain/bin/aarch64-buildroot-linux-gnu-" \
  LOCALVERSION=-tegra olddefconfig
make -C r35/kernel/kernel-5.10 O="$PWD/out35" ARCH=arm64 \
  CROSS_COMPILE="$PWD/toolchain/bin/aarch64-buildroot-linux-gnu-" \
  LOCALVERSION=-tegra -j"$(nproc)" Image modules dtbs
make -C r35/kernel/kernel-5.10 O="$PWD/out35" ARCH=arm64 \
  CROSS_COMPILE="$PWD/toolchain/bin/aarch64-buildroot-linux-gnu-" \
  LOCALVERSION=-tegra M="$PWD/driver" modules
make -C driver CC="$PWD/toolchain/bin/aarch64-buildroot-linux-gnu-gcc"
```

The kernel release must be `5.10.216-tegra`. If NVIDIA's prebuilt display
modules don't load with this kernel, build them from the R35.6.5 sources as
NVIDIA describes.

`driver/leopard_imx185.c` is generated from NVIDIA's driver. To regenerate it:

```sh
python3 prepare-sensor.py r35/kernel/nvidia/drivers/media/i2c/imx185.c \
  driver/profile.json driver
```

The script stops if the parts of NVIDIA's source that it changes differ from
R35.6.5.

## Device tree and rootfs

Generate the device tree after any kernel `make clean`, which also cleans
`driver/`:

```sh
python3 port-device-tree.py inputs/live-r32.4.3.dtb \
  stock/tegra194-p3668-0001-p3509-0000.dtb driver/leopard-r35.6.5.dtb
python3 test-evidence.py
bash install-r35-camera.sh "$PWD" inputs/camera_overrides.isp
```

`install-r35-camera.sh` writes into `bsp35/`, not to a device: the kernel,
modules, DTB, ISP file, module loading configuration and the `leopard-gmsl`
tool.

Prepare a login on the new rootfs:

```sh
bash setup-rootfs.sh bsp35/Linux_for_Tegra inputs/operator.pub operator leopard-xnx
```

This creates the account with passwordless sudo and the given SSH key, enables
SSH password login, and leaves the password locked; set one in the rootfs if
you need it. It also patches NVIDIA's OTA scripts so a missing DSA host key
type (OpenSSH 10 and later) is not fatal. To keep the box's SSH identity,
copy its host keys into the rootfs.

## Upgrade over the air

32.4.3 is too old to go straight to R35, so the upgrade has two image-based OTA
steps, each with target `jetson-xavier-nx-devkit-emmc`:

1. 32.4.3 → stock NVIDIA 32.7.3. Optionally use a device tree made with
   `port-device-tree.py --carrier-only`, which adds only the microSD wiring.
2. 32.7.3 → 35.6.5 with the camera support installed above.

For each step, prepare that release's BSP and rootfs (`setup-rootfs.sh`, and
for 35.6.5 the camera install above). Then follow NVIDIA's
[image-based OTA guide](https://docs.nvidia.com/jetson/archives/r35.6.5/DeveloperGuide/SD/SoftwarePackagesAndTheUpdateMechanism.html):
build the base recovery image with that release's OTA tools, and build the
payload with `l4t_generate_ota_package.sh`, with `BASE_BSP` pointing at the
BSP of the release the box runs (32.4.3, then 32.7.3) and `TARGET_BSP` at the
target release. Don't rename the rootfs
partition (`APP`). R32.7.3's
`bootloader/BUP_generator.py` and `bootloader/rollback/rollback_parser.py` need
Python 2: point their shebangs at a Python 2.7 build (2.7.18 builds on Ubuntu
26.04 with `CFLAGS='-O2 -std=gnu89'`) and leave the other tools on Python 3.

The 16 GB eMMC can't hold a second copy of the payload, so stage it from USB
storage. Build a disk image with the payload, the OTA tools and a staging
script:

```sh
bash make-ota-media.sh ota_payload_package.tar.gz ota_tools.tbz2 upgrade.img
```

The image is ext4 without the features R32's 4.9 kernel can't mount. Bring-up
attached it as virtual USB media from a GL.iNet Comet KVM;
`comet-media.py --url https://COMET upgrade.img` attaches an image already
uploaded to the Comet. A USB stick with the image written to it was not tried.

On the box, mount the disk and run `sudo bash /MOUNTPOINT/stage-ota.sh`. It
checks the files and starts NVIDIA's staging, reading the payload from USB.
When staging succeeds, unmount the disk and reboot. A console (HDMI and
keyboard, or the KVM) lets you follow the update.

Payloads contain the prepared accounts and SSH host keys. Delete them from the
build host, the USB storage and the box afterwards.

After the final step, hold NVIDIA's kernel packages so `apt` does not replace
the custom kernel:

```sh
sudo apt-mark hold nvidia-l4t-kernel nvidia-l4t-kernel-dtbs nvidia-l4t-kernel-headers
```

Then run the acceptance test from the [README](../README.md#testing-on-the-box).

For the USB port setup, see [porting notes](porting-notes.md#hardware).
