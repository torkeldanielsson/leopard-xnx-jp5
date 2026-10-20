#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Move the observed camera topology into an NVIDIA R35 Xavier NX DTB.

Preserves the R35 platform nodes and remaps every copied phandle reference.
Requires python3-libfdt. Inputs and output are explicit; never flashes a device.
"""
import argparse
import copy
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
import libfdt


@dataclass
class Node:
    name: str
    props: dict = field(default_factory=dict)
    children: dict = field(default_factory=dict)

    def at(self, path):
        node = self
        for part in path.strip('/').split('/'):
            if part:
                node = node.children[part]
        return node

    def walk(self, path=''):
        path = path.rstrip('/') + '/' + self.name
        yield path, self
        for child in self.children.values():
            yield from child.walk(path)


def read_node(fdt, offset=0):
    node = Node(fdt.get_name(offset))
    try:
        p = fdt.first_property_offset(offset)
        while p >= 0:
            prop = fdt.get_property_by_offset(p)
            node.props[prop.name] = bytes(prop)
            p = fdt.next_property_offset(p)
    except libfdt.FdtException:
        pass
    try:
        child = fdt.first_subnode(offset)
        while child >= 0:
            n = read_node(fdt, child)
            node.children[n.name] = n
            child = fdt.next_subnode(child)
    except libfdt.FdtException:
        pass
    return node


def cells(raw):
    return list(struct.unpack('>' + 'I'*(len(raw)//4), raw))


def raw_cells(values):
    return struct.pack('>' + 'I'*len(values), *values)


def phandles(root):
    return {cells(n.props['phandle'])[0]: (path, n)
            for path, n in root.walk() if 'phandle' in n.props}


def ordered_children(node):
    # A dtc -I fs snapshot does not preserve the original FDT sibling order.
    # NVIDIA's VI graph walker pairs endpoints with channels in sibling order,
    # while its port parser selects them by reg; both must agree.
    def key(item):
        name, child = item
        match = re.fullmatch(r'(.*)@([0-9a-f]+)', name)
        if match:
            return match[1], int(match[2], 16)
        match = re.fullmatch(r'(.*?)([0-9]+)', name)
        return (match[1], int(match[2])) if match else (name, -1)
    node.children = dict(sorted(node.children.items(), key=key))
    for child in node.children.values():
        ordered_children(child)


def port_carrier(old, base, usb0_host=False):
    """Carry over non-camera wiring shared by the R32 and R35 carrier ports."""
    old_handles = phandles(old)

    def provider(handle):
        path, node = old_handles[handle]
        return cells(base.at(path).props['phandle'])[0]

    def gpio(raw):
        value = cells(raw)
        value[0] = provider(value[0])
        return raw_cells(value)

    old_sd = old.at('/sdhci@3440000')
    if old_sd.props.get('status') == b'okay\0':
        new_sd = base.at('/sdhci@3440000')
        for name in ('status', 'mmc-ocr-mask', 'nvidia,cd-wakeup-capable', 'cd-inverted'):
            if name in old_sd.props:
                new_sd.props[name] = old_sd.props[name]
        new_sd.props['cd-gpios'] = gpio(old_sd.props['cd-gpios'])
        supply = cells(old_sd.props['vmmc-supply'])[0]
        new_supply = provider(supply)
        new_sd.props['vmmc-supply'] = raw_cells([new_supply])
        phandles(base)[new_supply][1].props['gpio'] = gpio(old_handles[supply][1].props['gpio'])

    if not usb0_host:
        return base

    # The original image forced USB2-0 into host mode. This optional override
    # restores that, e.g. for a keyboard on micro-USB. By default keep the
    # NVIDIA OTG setup, so micro-USB stays the recovery/device port.
    old_usb = old.at('/xusb_padctl@3520000/ports/usb2-0')
    assert old_usb.props['mode'] == b'host\0'
    old_supply = cells(old_usb.props['vbus-supply'])[0]
    regulator = copy.deepcopy(old_handles[old_supply][1])
    new_handle = max(phandles(base)) + 1
    regulator.props['phandle'] = raw_cells([new_handle])
    regulator.props.pop('linux,phandle', None)
    regulator.props['gpio'] = gpio(regulator.props['gpio'])
    base.at('/fixed-regulators').children[regulator.name] = regulator
    usb = base.at('/xusb_padctl@3520000/ports/usb2-0')
    usb.props.update({'mode': b'host\0', 'status': b'okay\0',
                      'vbus-supply': raw_cells([new_handle])})
    usb.props.pop('usb-role-switch', None)
    usb.children.pop('connector', None)
    base.at('/xudc@3550000').props['status'] = b'disabled\0'
    xhci = base.at('/xhci@3610000')
    for name in ('extcon-cables', 'extcon-cable-names'):
        xhci.props.pop(name, None)
    return base


def port(old, base):
    roots = [old.at('/i2c@3180000/tca9548@77'),
             old.at('/host1x/vi@15c10000/ports'),
             old.at('/tegra-camera-platform')]
    roots += [n for n in old.at('/host1x/nvcsi@15a00000').children.values()
              if n.name.startswith('channel@')]
    roots += [n for n in old.children.values() if n.name.startswith('lens_imx185@')]
    roots = copy.deepcopy(roots)
    for root in roots:
        ordered_children(root)
    roots.sort(key=lambda n: (n.name.split('@')[0],
                             int(n.name.split('@')[1], 16) if n.name.startswith('channel@') else 0))
    old_handles, base_handles = phandles(old), phandles(base)
    handle_map, new_handle = {}, max(base_handles) + 1
    for root in roots:
        for _, node in root.walk():
            if 'phandle' in node.props:
                handle_map[cells(node.props['phandle'])[0]] = new_handle
                new_handle += 1

    def remap(handle):
        if handle in handle_map:
            return handle_map[handle]
        path, old_node = old_handles[handle]
        if old_node.props.get('compatible') == b'nvidia,tegra-bpmp-clks\0':
            path = '/bpmp'
        try:
            new_node = base.at(path)
        except KeyError:
            # Regulator paths can change between platform device trees.
            name = old_node.props.get('regulator-name')
            matches = [n for _, n in base.walk() if name and n.props.get('regulator-name') == name]
            if len(matches) != 1:
                raise ValueError('cannot map provider: ' + path)
            new_node = matches[0]
        result = cells(new_node.props['phandle'])[0]
        handle_map[handle] = result
        return result

    for root in roots:
        for _, node in root.walk():
            for key, value in list(node.props.items()):
                if key in ('phandle', 'linux,phandle', 'remote-endpoint') or key.endswith('-supply'):
                    node.props[key] = raw_cells([remap(cells(value)[0])])
                elif key in ('reset-gpios', 'pwdn-gpios'):
                    v = cells(value); v[0] = remap(v[0]); node.props[key] = raw_cells(v)
                elif key == 'clocks':
                    v = cells(value)
                    for i in range(0, len(v), 2):
                        v[i] = remap(v[i])
                    node.props[key] = raw_cells(v)
                elif key == 'devname' and value.startswith(b'imx185 '):
                    node.props[key] = value.replace(b'imx185 ', b'li-imx185 ', 1)
            if node.props.get('compatible') == b'nvidia,imx185\0':
                node.props['compatible'] = b'leopard,li-imx185\0'
                # The recovered MAX9296 configuration uses 1.5 Gbit/s per lane.
                # R35 converts SerDes pixel rate to lane rate and then divides
                # by two for D-PHY's DDR clock. The old 1.5 GHz value encoded
                # lane rate here and would request a 4.5 GHz clock in R35.
                mode = node.children['mode0']
                lanes = int(mode.props['num_lanes'].rstrip(b'\0'))
                depth = int(mode.props['csi_pixel_bit_depth'].rstrip(b'\0'))
                mode.props['serdes_pix_clk_hz'] = str(1500000000 * lanes // depth).encode() + b'\0'
            elif node.props.get('compatible') == b'nvidia,max929x\0':
                node.props['compatible'] = b'leopard,li-max929x\0'
    for root in roots:
        if root.name == 'tca9548@77':
            # R35's pca954x driver reads the parent property; R32 also accepted
            # i2c-mux,deselect-on-exit on each child bus. Retain bus isolation.
            root.props['i2c-mux-idle-disconnect'] = b''
            base.at('/i2c@3180000').children[root.name] = root
        elif root.name == 'ports':
            # R35 separates the capture graph from the VI hardware device.
            base.at('/tegra-capture-vi').children['ports'] = root
        elif root.name.startswith('channel@'):
            base.at('/host1x@13e00000/nvcsi@15a00000').children[root.name] = root
        else:
            base.children[root.name] = root
    for path in ['/tegra-capture-vi', '/host1x@13e00000/nvcsi@15a00000']:
        base.at(path).props.update({'status': b'okay\0', 'num-channels': raw_cells([6])})
    base.at('/i2c@3180000').props.update({'status': b'okay\0', 'clock-frequency': raw_cells([400000])})
    # Devkit ribbon-camera endpoints belong to the graph being replaced.
    # Leaving them enabled would point the IMX219 driver at removed endpoints.
    if 'cam_i2cmux' in base.children:
        base.children['cam_i2cmux'].props['status'] = b'disabled\0'
    port_carrier(old, base)
    # Preserve the carrier's four fixed camera control outputs, including
    # PQ.03 and PR.00 which are absent from the two-camera devkit defaults.
    base.at('/gpio@2200000').children['camera-control-output-low'] = copy.deepcopy(
        old.at('/gpio@2200000/camera-control-output-low'))
    # This carrier uses AON GPIO 16 for camera power, not the devkit's radio disable.
    aon = base.at('/gpio@c2f0000')
    for name, node in list(aon.children.items()):
        if 'gpio-hog' in node.props and cells(node.props.get('gpios', b''))[:1] == [16]:
            del aon.children[name]
    # Remove labels whose nodes were replaced; all retained labels still resolve.
    symbols = base.children.get('__symbols__')
    if symbols:
        for name, value in list(symbols.props.items()):
            try:
                base.at(value.rstrip(b'\0').decode())
            except KeyError:
                del symbols.props[name]
    # Graph reciprocity proves the six sensor -> CSI -> VI paths stayed connected.
    handles = phandles(base)
    for path, node in (item for root in roots for item in root.walk()):
        if 'remote-endpoint' in node.props:
            target = cells(node.props['remote-endpoint'])[0]
            peer = handles[target][1]
            if peer.props.get('remote-endpoint') != node.props.get('phandle'):
                raise ValueError('non-reciprocal endpoint: ' + path)
    vi_ports = base.at('/tegra-capture-vi/ports')
    if [cells(n.props['reg'])[0] for n in vi_ports.children.values()] != list(range(6)):
        raise ValueError('VI endpoint order must match channel indices 0 through 5')
    sensors = [n for _, n in base.walk()
               if n.props.get('compatible') == b'leopard,li-imx185\0']
    if sorted(cells(n.props['channel'])[0] for n in sensors) != list(range(6)):
        raise ValueError('expected exactly six uniquely numbered IMX185 sensors')
    return base


def write_node(sw, node):
    sw.begin_node(node.name)
    for name, value in node.props.items():
        sw.property(name, value)
    for child in node.children.values():
        write_node(sw, child)
    sw.end_node()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--carrier-only', action='store_true', help='SD wiring only, for the intermediate R32 image')
    p.add_argument('--usb0-host', action='store_true', help='legacy micro-USB host wiring; do not use with micro-USB connected to a host')
    p.add_argument('original', type=Path); p.add_argument('base', type=Path)
    p.add_argument('output', type=Path); a = p.parse_args()
    old_fdt = libfdt.Fdt(a.original.read_bytes()); base_fdt = libfdt.Fdt(a.base.read_bytes())
    root = (port_carrier if a.carrier_only else port)(read_node(old_fdt), read_node(base_fdt))
    if a.usb0_host:
        root = port_carrier(read_node(old_fdt), root, usb0_host=True)
    sw = libfdt.FdtSw()
    for i in range(base_fdt.num_mem_rsv()):
        addr, size = base_fdt.get_mem_rsv(i)
        sw.add_reservemap_entry(addr, size)
    sw.finish_reservemap(); write_node(sw, root)
    a.output.write_bytes(sw.as_fdt().as_bytearray())
