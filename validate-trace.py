#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify the recovered link table against successful physical I2C transfers."""
import argparse
import json
import re
from pathlib import Path

WRITE = re.compile(r'(\d+\.\d+): i2c_write: i2c-(\d+) #\d+ a=([0-9a-f]+) f=([0-9a-f]+) l=(\d+) \[([0-9a-f-]*)\]')
RESULT = re.compile(r'i2c_result: i2c-(\d+) n=(\d+) ret=(-?\d+)')


def transfers(trace):
    pending, writes = {}, {30: [], 31: [], 32: []}
    for line in trace.splitlines():
        m = WRITE.search(line)
        if m:
            time, bus, addr, flags, length, raw = m.groups()
            bus = int(bus)
            if bus not in writes:
                continue
            # These traces initialize links with single three-byte writes.
            # Linux 5.10 marks userspace I2C_RDWR buffers I2C_M_DMA_SAFE.
            # This flag does not alter the transfer on the wire.
            if int(flags, 16) & ~0x0200 or int(length) != 3:
                continue
            payload = bytes.fromhex(raw.replace('-', ''))
            pending[bus] = (float(time), int(addr, 16), int.from_bytes(payload[:2], 'big'), payload[2])
        m = RESULT.search(line)
        if m:
            bus, n, result = map(int, m.groups())
            if bus in pending:
                row = pending.pop(bus)
                if n != 1 or result != 1:
                    raise ValueError(f'failed transfer on bus {bus}: {result}/{n}')
                writes[bus].append(row)
    if pending:
        raise ValueError('trace ends with incomplete transfers')
    return writes


def validate(profile, trace):
    rows = transfers(trace)
    expected = [(w['address'], w['reg'], w['value']) for w in profile['link_init']]
    for bus, actual in rows.items():
        if [w[1:] for w in actual] != expected:
            raise ValueError(f'bus {bus}: register sequence differs ({len(actual)} writes)')
        for i, w in enumerate(profile['link_init'][:-1]):
            # Scheduler and timestamp resolution tolerance: 5 ms.
            if w['delay_ms'] and actual[i+1][0] - actual[i][0] < (w['delay_ms'] - 5)/1000:
                raise ValueError(f'bus {bus}: missing delay after write {i}')
    return {'buses': sorted(rows), 'writes_per_bus': len(expected), 'match': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('profile', type=Path); p.add_argument('trace', type=Path)
    a = p.parse_args()
    print(json.dumps(validate(json.loads(a.profile.read_text()), a.trace.read_text()), indent=2))
