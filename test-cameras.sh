#!/bin/bash
# SPDX-License-Identifier: MIT
# Hardware acceptance on the target. Writes logs and raw samples to OUTPUT.
# No clients may hold the cameras while this runs. Run as root.
set -euo pipefail
out=$(realpath -m "${1:?output directory}")
script_dir=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
test "$(id -u)" = 0
command -v v4l2-ctl >/dev/null
finish() {
    dmesg > "$out/dmesg.txt"
    systemctl start nvargus-daemon
}
trap finish EXIT
systemctl stop nvargus-daemon
uname -a > "$out/kernel.txt"
cat /etc/nv_tegra_release > "$out/l4t.txt"
media-ctl -p > "$out/media.txt"
lsmod > "$out/modules.txt"
for camera in 0 1 2 3 4 5; do
    v4l2-ctl -d "/dev/video$camera" --all > "$out/video$camera-info.txt"
    timeout -k 3 25 v4l2-ctl -d "/dev/video$camera" \
        --set-fmt-video=width=1920,height=1080,pixelformat=RG12 \
        --set-ctrl=bypass_mode=0 --stream-mmap --stream-count=90 \
        --stream-to=/dev/null > "$out/video$camera-single.log" 2>&1
    echo "camera=$camera sequential=PASS"
done
pids=()
for camera in 0 1 2 3 4 5; do
    timeout -k 3 30 v4l2-ctl -d "/dev/video$camera" \
        --set-ctrl=bypass_mode=0 --stream-mmap --stream-count=300 \
        --stream-to=/dev/null > "$out/video$camera-parallel.log" 2>&1 &
    pids+=("$!")
done
result=0
for camera in 0 1 2 3 4 5; do
    if wait "${pids[$camera]}"; then
        echo "camera=$camera simultaneous=PASS"
    else
        echo "camera=$camera simultaneous=FAIL"
        result=1
    fi
done
dmesg > "$out/dmesg.txt"
test "$result" = 0
# Capture one raw frame from each camera for content/size checks.
for camera in 0 1 2 3 4 5; do
    timeout -k 3 10 v4l2-ctl -d "/dev/video$camera" --set-ctrl=bypass_mode=0 \
        --stream-mmap --stream-count=1 --stream-to="$out/video$camera.raw" \
        > "$out/video$camera-sample.log" 2>&1
done
systemctl start nvargus-daemon
for camera in 0 1 2 3 4 5; do
    timeout -k 3 30 python3 "$script_dir/test-argus.py" "$camera" \
        > "$out/argus$camera.log" 2>&1
    echo "camera=$camera Argus=PASS frames=120"
done
echo 'Six-camera acceptance PASS'
