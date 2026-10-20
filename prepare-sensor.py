#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply the observed Leopard differences to NVIDIA's R35.6.5 IMX185 driver."""
import argparse
import json
import re
from pathlib import Path


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('source does not match expected NVIDIA driver: ' + old[:80])
    return source.replace(old, new, 1)


def prepare(source):
    source = replace_once(source, '#include "imx185_mode_tbls.h"',
        '#include "leopard_imx185_mode_tbls.h"\n#include "leopard_gmsl.h"')
    source = replace_once(source, '"sony,imx185"', '"leopard,li-imx185"')
    source = replace_once(source, '\tstruct tegracam_device\t\t*tc_dev;\n',
        '\tstruct tegracam_device\t\t*tc_dev;\n\tu32 channel;\n')
    start = source.index('static int imx185_write_table(')
    end = source.index('\nstatic int imx185_set_group_hold', start)
    source = source[:start] + '''static int imx185_write_table(struct imx185 *priv,
                                const imx185_reg table[])
{
    int err, retry;
    for (; table->addr != IMX185_TABLE_END; table++) {
        if (table->addr == IMX185_TABLE_WAIT_MS) {
            msleep(table->val);
            continue;
        }
        for (retry = 0; retry < 10; retry++) {
            err = imx185_write_reg(priv->s_data, table->addr, table->val);
            if (!err)
                break;
            if (retry < 9)
                msleep(4);
        }
        if (err)
            return err;
    }
    return 0;
}
''' + source[end:]
    start = source.index('\t/*exit reset mode: XCLR */')
    end = source.index('\n\tpw->state = SWITCH_ON;', start)
    source = source[:start] + '\t/* GMSL serializers own sensor reset and the 27 MHz clock. */\n' + source[end:]
    start = source.index('\t/* enter reset mode: XCLR */')
    end = source.index('\npower_off_done:', start)
    source = source[:start] + source[end:]
    source = replace_once(source, 'if (s_data->mode_prop_idx < 0)',
        'if (s_data->mode_prop_idx != 0)')
    start = source.index('static int imx185_start_streaming(')
    end = source.index('static int imx185_stop_streaming(', start)
    part = source[start:end]
    part = replace_once(part, '\treturn 0;\n}',
        '\terr = leopard_gmsl_stream(priv->channel, true);\n'
        '\tif (err)\n\t\timx185_write_table(priv, mode_table[IMX185_MODE_STOP_STREAM]);\n'
        '\treturn err;\n}')
    source = source[:start] + part + source[end:]
    source = replace_once(source, '\t/* SW_RESET will have no ACK */',
        '\terr = leopard_gmsl_stream(priv->channel, false);\n\tif (err)\n\t\treturn err;\n\n'
        '\t/* SW_RESET will have no ACK */')
    start = source.index('static int imx185_probe(')
    part = source[start:]
    part = replace_once(part, '\tdev_info(dev, "probing v4l2 sensor\\n");',
        '\tdev_dbg(dev, "probing v4l2 sensor\\n");')
    part = replace_once(part, '\ttc_dev = devm_kzalloc(dev,',
        '\terr = of_property_read_u32(dev->of_node, "channel", &priv->channel);\n'
        '\tif (err || priv->channel >= 6)\n\t\treturn -EINVAL;\n'
        '\terr = leopard_gmsl_ready(priv->channel / 2);\n'
        '\tif (err)\n\t\treturn err;\n\n\ttc_dev = devm_kzalloc(dev,')
    source = source[:start] + part
    # The original Leopard driver omits fuse reads, but a real register read here
    # verifies the sensor acknowledges before registering it with the VI driver.
    start = source.index('static int imx185_fuse_id_setup(')
    end = source.index('static int imx185_board_setup(', start)
    source = source[:start] + source[end:]
    source = replace_once(source, '\terr = imx185_fuse_id_setup(priv);',
        '\t{\n\t\tu8 standby;\n\t\terr = imx185_read_reg(s_data, 0x3000, &standby);\n\t}')
    source = source.replace('reading fuse id data', 'reading sensor standby register')
    # VI prefixes the name with "vi-output, "; its 32-byte card field must
    # still contain the full bus/address for Argus to identify each sensor.
    source = replace_once(source, '{ "imx185", 0 }', '{ "li-imx185", 0 }')
    source = replace_once(source, '.name = "imx185"', '.name = "li-imx185"')
    source += '\nMODULE_SOFTDEP("pre: leopard_gmsl");\n'
    return ('// SPDX-License-Identifier: GPL-2.0-only\n'
            '// Modified 2026-10-19 for the Leopard Xavier NX JetPack 5 port.\n' + source)


def tables(profile):
    s = ['/* SPDX-License-Identifier: GPL-2.0-only */',
         '/* Recovered sensor register configuration; see profile.json for provenance. */',
         '#ifndef LEOPARD_IMX185_TABLES_H', '#define LEOPARD_IMX185_TABLES_H',
         '#include <media/camera_common.h>', '#define imx185_reg struct reg_8',
         '#define IMX185_TABLE_WAIT_MS 0', '#define IMX185_TABLE_END 1']
    names = [('sensor_mode', 'imx185_mode'), ('sensor_start', 'imx185_start'),
             ('sensor_stop', 'imx185_stop'), ('sensor_test_pattern', 'imx185_test')]
    for key, name in names:
        s.append('static const imx185_reg ' + name + '[] = {')
        s += ['    {0x%04x, 0x%02x},' % (r['reg'], r['value']) for r in profile[key]]
        s += ['    {IMX185_TABLE_END, 0},', '};']
    s += ['enum { IMX185_MODE_1080P_30FPS, IMX185_MODE_START_STREAM,',
          '       IMX185_MODE_STOP_STREAM, IMX185_MODE_TEST_PATTERN };',
          'static const imx185_reg *mode_table[] = {',
          '    imx185_mode, imx185_start, imx185_stop, imx185_test,', '};',
          'static const int imx185_30fps[] = {30};',
          'static const struct camera_common_frmfmt imx185_frmfmt[] = {',
          '    {{1920, 1080}, imx185_30fps, 1, 0, IMX185_MODE_1080P_30FPS},',
          '};', '#endif', '']
    return '\n'.join(s)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path); p.add_argument('profile', type=Path)
    p.add_argument('output', type=Path); a = p.parse_args()
    a.output.mkdir(exist_ok=True, parents=True)
    (a.output/'leopard_imx185.c').write_text(prepare(a.source.read_text()))
    (a.output/'leopard_imx185_mode_tbls.h').write_text(tables(json.loads(a.profile.read_text())))
