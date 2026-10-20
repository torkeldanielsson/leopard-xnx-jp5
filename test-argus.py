#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Require real Argus buffers; nvarguscamerasrc can return EOS on failure."""
import argparse
from pathlib import Path
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('camera', type=int)
p.add_argument('--frames', type=int, default=120)
p.add_argument('--jpeg', type=Path)
a = p.parse_args()
Gst.init(None)
tail = ''
if a.jpeg:
    tail = ('nvvidconv ! video/x-raw,width=640,height=360,format=I420 ! '
            'jpegenc ! ')
pipeline = Gst.parse_launch(
    f'nvarguscamerasrc sensor-id={a.camera} num-buffers={a.frames} ! '
    'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1,format=NV12 ! '
    + tail + 'appsink name=frames emit-signals=true sync=false max-buffers=2')
timestamps = []
last_jpeg = None


def sample(sink):
    global last_jpeg
    frame = sink.emit('pull-sample')
    if frame is None:
        return Gst.FlowReturn.ERROR
    buffer = frame.get_buffer()
    if buffer.get_size() == 0:
        return Gst.FlowReturn.ERROR
    timestamps.append(buffer.pts)
    if a.jpeg:
        last_jpeg = buffer.extract_dup(0, buffer.get_size())
    return Gst.FlowReturn.OK


pipeline.get_by_name('frames').connect('new-sample', sample)
try:
    pipeline.set_state(Gst.State.PLAYING)
    message = pipeline.get_bus().timed_pop_filtered(
        25 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
    if message is None:
        raise RuntimeError('Argus capture timed out')
    if message.type == Gst.MessageType.ERROR:
        raise RuntimeError(str(message.parse_error()))
    if len(timestamps) != a.frames:
        raise RuntimeError(f'expected {a.frames} frames, received {len(timestamps)}')
    if any(t == Gst.CLOCK_TIME_NONE for t in timestamps):
        raise RuntimeError('missing frame timestamps')
    if any(b <= c for c, b in zip(timestamps, timestamps[1:])):
        raise RuntimeError('frame timestamps did not increase')
    if a.jpeg:
        a.jpeg.write_bytes(last_jpeg)
    print(f'camera={a.camera} Argus=PASS frames={len(timestamps)}')
finally:
    pipeline.set_state(Gst.State.NULL)
