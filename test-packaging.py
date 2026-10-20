#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline checks for generalized helpers and reproducible register headers."""
import contextlib
import importlib.util
import io
import json
import subprocess
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Packaging(unittest.TestCase):
    def test_generated_register_headers_match_profile(self):
        profile = json.loads((ROOT / 'driver/profile.json').read_text())
        self.assertEqual(load('extract-profile').header(profile),
                         (ROOT / 'driver/leopard_tables.h').read_text())
        self.assertEqual(load('prepare-sensor').tables(profile),
                         (ROOT / 'driver/leopard_imx185_mode_tbls.h').read_text())

    def comet(self, args):
        module = load('comet-media')
        session = MagicMock()
        session.__enter__.return_value = session
        session.cookies.get.return_value = 'test-session'
        session.get.return_value.json.return_value = {'result': {'drive': {'connected': True}}}
        session.post.return_value.json.return_value = {'ok': True}
        argv = ['comet-media.py', '--url', 'https://comet.example', '--password-stdin', *args]
        with patch.object(module.requests, 'Session', return_value=session), \
                patch.object(sys, 'argv', argv), patch.object(sys, 'stdin', io.StringIO('test-only\n')), \
                contextlib.redirect_stdout(io.StringIO()):
            module.main()
        return session

    def test_attach_disconnects_before_selecting_new_media(self):
        session = self.comet(['upgrade.img'])
        self.assertIs(session.verify, True)
        self.assertEqual([call.args[0] for call in session.post.call_args_list], [
            'https://comet.example/api/auth/login',
            'https://comet.example/api/msd/set_connected',
            'https://comet.example/api/msd/set_params',
            'https://comet.example/api/msd/set_connected',
        ])
        self.assertEqual(session.post.call_args_list[1].kwargs['params'], {'connected': '0'})
        self.assertEqual(session.post.call_args_list[2].kwargs['params'],
                         {'image': 'upgrade.img', 'cdrom': '0', 'rw': '1'})
        self.assertEqual(session.post.call_args_list[3].kwargs['params'], {'connected': '1'})

    def test_detach_does_not_select_or_reconnect_media(self):
        session = self.comet(['--insecure', 'detach'])
        self.assertIs(session.verify, False)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(session.post.call_args.kwargs['params'], {'connected': '0'})

    def test_invalid_media_rejected_before_authentication_or_detach(self):
        module = load('comet-media')
        with patch.object(module.requests, 'Session') as session, \
                patch.object(module.getpass, 'getpass') as password, \
                patch.object(sys, 'argv', ['comet-media.py', '--url', 'https://comet.example', '../bad.img']), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            module.main()
        self.assertEqual(raised.exception.code, 2)
        session.assert_not_called()
        password.assert_not_called()


    def test_unconfigured_reset_hook_exits_before_hardware_access(self):
        result = subprocess.run(['sh', str(ROOT / 'leopard-pmic-reset'), '--check'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Set the exact module serial', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
