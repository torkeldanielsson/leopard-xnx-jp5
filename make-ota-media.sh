#!/bin/bash
# SPDX-License-Identifier: MIT
# Build a USB disk image for OTA staging. No physical drive is touched here.
set -euo pipefail
umask 077
payload=$(realpath "$1")
ota_tools=$(realpath "$2")
output=$(realpath -m "$3")
test ! -e "$output"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp "$payload" "$stage/ota_payload_package.tar.gz"
cp "$ota_tools" "$stage/ota_tools.tbz2"
(cd "$stage"; sha256sum ota_payload_package.tar.gz ota_tools.tbz2 > SHA256SUMS)
cat > "$stage/stage-ota.sh" <<'SCRIPT'
#!/bin/bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")"
sha256sum -c SHA256SUMS
test "$(id -u)" = 0
payload=$(realpath ota_payload_package.tar.gz)
mkdir -p /opt/leopard-ota
# Read the outer archive directly from USB. Keeping another copy on the
# 16 GB eMMC leaves insufficient room for NVIDIA's two extraction stages.
tar -xpf ota_tools.tbz2 -C /opt/leopard-ota
cd /opt/leopard-ota/Linux_for_Tegra/tools/ota_tools/version_upgrade
exec ./nv_ota_start.sh "$payload"
SCRIPT
chmod 755 "$stage/stage-ota.sh"
size=$(du -sb "$stage" | cut -f1)
truncate -s "$((size + 256*1024*1024))" "$output"
# Disable features unsupported by the original 4.9 recovery kernel.
mkfs.ext4 -q -F -L LEOPARD_OTA -O '^metadata_csum_seed,^orphan_file' -d "$stage" "$output"
sha256sum "$output"
