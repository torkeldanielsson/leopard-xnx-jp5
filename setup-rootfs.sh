#!/bin/bash
# SPDX-License-Identifier: MIT
# Prepare access to an offline NVIDIA rootfs on the build machine.
# This deliberately leaves the password locked; set one in the rootfs if needed.
set -euo pipefail
bsp=$(realpath "${1:?usage: setup-rootfs.sh BSP SSH_PUBLIC_KEY USERNAME HOSTNAME}")
key=$(realpath "${2:?SSH public key required}")
account=${3:?username required}
target_hostname=${4:?hostname required}
[[ "$account" =~ ^[a-z_][a-z0-9_-]*$ ]]
[[ "$account" != root ]]
[[ "$target_hostname" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]
cd "$bsp"
if ! awk -F: -v name="$account" '$1 == name {found=1} END {exit !found}' rootfs/etc/passwd; then
    # A random password nobody knows; it is locked below.
    sudo ./tools/l4t_create_default_user.sh -u "$account" \
        -p "$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')" \
        -n "$target_hostname" --accept-license >/dev/null
fi
sudo chroot rootfs passwd -l "$account" >/dev/null
uid=$(awk -F: -v name="$account" '$1==name {print $3}' rootfs/etc/passwd)
gid=$(awk -F: -v name="$account" '$1==name {print $4}' rootfs/etc/passwd)
sudo install -d -m 700 -o "$uid" -g "$gid" "rootfs/home/$account/.ssh"
sudo install -m 600 -o "$uid" -g "$gid" "$key" "rootfs/home/$account/.ssh/authorized_keys"
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$account" | sudo tee rootfs/etc/sudoers.d/90-leopard-dev >/dev/null
sudo chmod 440 rootfs/etc/sudoers.d/90-leopard-dev
sudo install -d rootfs/etc/ssh/sshd_config.d
echo 'PasswordAuthentication yes' | sudo tee rootfs/etc/ssh/sshd_config.d/10-leopard-dev.conf >/dev/null
# Avoid producing obsolete DSA host keys on modern OpenSSH build hosts.
sudo sed -i '/ -t dsa /s/;check_error$/ || true/' \
    tools/ota_tools/version_upgrade/ota_make_recovery_img_dtb.sh \
    tools/ota_tools/version_upgrade/build_base_recovery_image.sh
echo "Prepared $(basename "$(dirname "$bsp")") with a locked password; set one before packaging for password login."
