#!/bin/bash
# SPDX-License-Identifier: MIT
# Capture I2C traffic while reprobeing links and streaming.
set -euo pipefail
out=${1:?output directory required}
driver=${2:-max929x}
case "$driver" in max929x|leopard-max929x) ;; *) exit 2 ;; esac
mkdir -p "$out"
trace=/sys/kernel/debug/tracing/instances/leopard
mkdir "$trace"
cleanup() { echo 0 > "$trace/tracing_on"; echo 0 > "$trace/events/enable"; rmdir "$trace"; }
trap cleanup EXIT
echo 8192 > "$trace/buffer_size_kb"
echo mono > "$trace/trace_clock"
echo 'adapter_nr == 2 || (adapter_nr >= 30 && adapter_nr <= 32)' > "$trace/events/i2c/filter"
echo 1 > "$trace/events/i2c/enable"
echo 1 > "$trace/events/regmap/regmap_reg_write/enable"
echo 1 > "$trace/tracing_on"
echo 'BEGIN MAX929X REPROBE' > "$trace/trace_marker"
for bus in 30 31 32; do
    echo "$bus-0048" > "/sys/bus/i2c/drivers/$driver/unbind"
done
for bus in 30 31 32; do
    echo "PROBE $bus-0048" > "$trace/trace_marker"
    echo "$bus-0048" > "/sys/bus/i2c/drivers/$driver/bind"
done
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$out/reprobe.trace"
cat "$trace"/per_cpu/cpu*/stats > "$out/reprobe.stats"
for camera in 0 1 2 3 4 5; do
    echo > "$trace/trace"
    echo 1 > "$trace/tracing_on"
    echo "STREAM $camera" > "$trace/trace_marker"
    set +e
    timeout -k 3 20 v4l2-ctl -d "/dev/video$camera" \
        --set-fmt-video=width=1920,height=1080,pixelformat=RG12 \
        --set-ctrl=bypass_mode=0 \
        --stream-mmap --stream-count=60 --stream-to=/dev/null \
        > "$out/video$camera.log" 2>&1
    result=$?
    set -e
    echo "$result" > "$out/video$camera.exit"
    echo 0 > "$trace/tracing_on"
    cat "$trace/trace" > "$out/video$camera.trace"
    cat "$trace"/per_cpu/cpu*/stats > "$out/video$camera.stats"
    echo "camera=$camera exit=$result"
done
dmesg > "$out/dmesg.txt"
