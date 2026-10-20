#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Extract register data identified with Ghidra; no executable code is copied."""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def extract(image, symbols):
    data = Path(image).read_bytes()
    syms = {}
    duplicates = {}
    for line in Path(symbols).read_text().splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms.setdefault(parts[2], int(parts[0], 16))
            duplicates.setdefault(parts[2], []).append(int(parts[0], 16))
    base = syms['_text']
    def offset(name):
        pos = syms[name] - base
        if not 0 <= pos < len(data):
            raise ValueError('symbol outside Image: ' + name)
        return pos
    def table(name):
        pos = offset(name)
        result = []
        for i in range(1024):
            reg, value = struct.unpack_from('<HB', data, pos + i * 4)
            if reg == 1:
                return result
            result.append({'reg': reg, 'value': value})
        raise ValueError('unterminated sensor table: ' + name)
    links = []
    # Several sensor drivers define tp_colorbars. Ghidra's Leopard IMX185
    # start routine references the six-entry table immediately before its
    # imx185_start, not the first unrelated tp_colorbars in kallsyms.
    test_pattern_address = syms['imx185_start'] - 6 * 4
    if test_pattern_address not in duplicates.get('tp_colorbars', []):
        raise ValueError('IMX185 test-pattern table layout changed')
    syms['tp_colorbars'] = test_pattern_address
    # Ghidra: max929x_probe passes count 0x3b, entries are three u16s.
    for i in range(59):
        address, reg, value = struct.unpack_from('<HHH', data,
            offset('max929x_Double_Dser_Ser_init') + i * 6)
        if address not in (0x48, 0x60, 0x62) or value > 255:
            raise ValueError('unexpected link register data')
        links.append({'address': address, 'reg': reg, 'value': value,
                      'delay_ms': 300 if reg in (0, 0x10) else 0})
    return {'model': 'LI-XNX-BOX-GMSL2/IMX185', 'source_l4t': '32.4.3',
            'image_sha256': hashlib.sha256(data).hexdigest(),
            'initial_delay_ms': 500, 'link_init': links,
            'sensor_mode': table('imx185_1920x1080_crop_30fps'),
            'sensor_start': table('imx185_start'),
            'sensor_stop': table('imx185_stop'),
            'sensor_test_pattern': table('tp_colorbars')}


def header(profile):
    lines = ['/* SPDX-License-Identifier: GPL-2.0-only */',
             '/* Register data recovered from Image SHA-256: ' + profile['image_sha256'] + ' */',
             '#ifndef LEOPARD_TABLES_H', '#define LEOPARD_TABLES_H',
             'struct leopard_reg { unsigned char address; unsigned short reg; unsigned char value; unsigned short delay_ms; };',
             'static const struct leopard_reg leopard_link_init[] = {']
    for w in profile['link_init']:
        lines.append('    {0x%02x, 0x%04x, 0x%02x, %d},' %
                     (w['address'], w['reg'], w['value'], w['delay_ms']))
    lines.extend(['};', '#endif', ''])
    return '\n'.join(lines)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image'); p.add_argument('symbols'); p.add_argument('output', type=Path)
    a = p.parse_args()
    profile = extract(a.image, a.symbols)
    a.output.mkdir(parents=True, exist_ok=True)
    (a.output / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    (a.output / 'leopard_tables.h').write_text(header(profile))
