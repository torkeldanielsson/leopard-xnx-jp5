#!/bin/bash
# SPDX-License-Identifier: MIT
# Install the built kernel, modules and recovered camera configuration offline.
set -euo pipefail
work=$(realpath "$1")
isp=$(realpath "$2")
bsp="$work/bsp35/Linux_for_Tegra"
rootfs="$bsp/rootfs"
release=$(cat "$work/out35/include/config/kernel.release")
test "$release" = 5.10.216-tegra
test -s "$work/out35/Module.symvers"
sudo make -C "$work/r35/kernel/kernel-5.10" O="$work/out35" ARCH=arm64 \
    CROSS_COMPILE="$work/toolchain/bin/aarch64-buildroot-linux-gnu-" \
    INSTALL_MOD_PATH="$rootfs" INSTALL_MOD_STRIP=1 modules_install
sudo install -Dm644 "$work/driver/leopard_gmsl.ko" "$rootfs/lib/modules/$release/extra/leopard_gmsl.ko"
sudo install -Dm644 "$work/driver/leopard_imx185.ko" "$rootfs/lib/modules/$release/extra/leopard_imx185.ko"
sudo depmod -b "$rootfs" "$release"
sudo install -m644 "$work/out35/arch/arm64/boot/Image" "$bsp/kernel/Image"
sudo install -m644 "$work/out35/arch/arm64/boot/Image" "$rootfs/boot/Image"
sudo install -m644 "$work/driver/leopard-r35.6.5.dtb" "$bsp/kernel/dtb/tegra194-p3668-0001-p3509-0000.dtb"
sudo install -Dm644 "$work/driver/leopard-r35.6.5.dtb" "$rootfs/boot/dtb/tegra194-p3668-0001-p3509-0000.dtb"
sudo install -Dm644 "$work/driver/leopard-r35.6.5.dtb" "$rootfs/boot/dtb/kernel_tegra194-p3668-0001-p3509-0000.dtb"
sudo install -Dm644 "$isp" "$rootfs/var/nvidia/nvcam/settings/camera_overrides.isp"
printf 'leopard_gmsl\nleopard_imx185\n' | sudo tee "$rootfs/etc/modules-load.d/leopard.conf" >/dev/null
"$work/toolchain/bin/aarch64-buildroot-linux-gnu-gcc" -O2 -Wall -Wextra -Werror \
    -o "$work/driver/leopard-gmsl" "$work/driver/leopard-gmsl.c"
sudo install -m755 "$work/driver/leopard-gmsl" "$rootfs/usr/local/sbin/leopard-gmsl"
modinfo -b "$rootfs" -k "$release" leopard_imx185
