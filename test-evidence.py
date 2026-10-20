#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check recovered register data against physical traces, including negative cases."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('trace', ROOT / 'validate-trace.py')
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


class HardwareEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads((ROOT / 'driver/profile.json').read_text())
        cls.baseline = ROOT / 'evidence/leopard-baseline-raw'
        cls.links = (cls.baseline / 'reprobe.trace').read_text()

    def test_link_writes_match_all_three_pairs(self):
        result = trace.validate(self.profile, self.links)
        self.assertEqual(result['writes_per_bus'], 59)

    def test_jp5_dma_safe_transfers_match(self):
        result = trace.validate(self.profile, (ROOT / 'evidence/jp5-mux-reinit.trace').read_text())
        self.assertEqual(result['writes_per_bus'], 59)

    def test_original_boot_failure_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'failed transfer'):
            trace.validate(self.profile, (ROOT / 'evidence/leopard-boot.trace').read_text())

    def test_corrupt_register_data_is_rejected(self):
        corrupted = self.links.replace('[00-10-21]', '[00-10-22]', 1)
        self.assertNotEqual(corrupted, self.links)
        with self.assertRaisesRegex(ValueError, 'sequence differs'):
            trace.validate(self.profile, corrupted)

    def test_truncated_trace_is_rejected(self):
        with self.assertRaises(ValueError):
            trace.validate(self.profile, self.links[:len(self.links)//2])

    def test_mode_writes_match_all_six_sensors(self):
        expected = [(w['reg'], w['value']) for w in self.profile['sensor_mode'] if w['reg'] > 1]
        for camera in range(6):
            with self.subTest(camera=camera):
                pending, successful, failed = {}, [], []
                for line in (self.baseline / f'video{camera}.trace').read_text().splitlines():
                    m = trace.WRITE.search(line)
                    if m:
                        time, bus, addr, flags, length, raw = m.groups()
                        bus = int(bus)
                        if bus == 30 + camera // 2 and int(length) == 3 and int(addr, 16) == (0x28 if camera % 2 == 0 else 0x2a):
                            b = bytes.fromhex(raw.replace('-', ''))
                            pending[bus] = (int.from_bytes(b[:2], 'big'), b[2])
                    m = trace.RESULT.search(line)
                    if m:
                        bus, count, result = map(int, m.groups())
                        if bus in pending:
                            (successful if count == result == 1 else failed).append(pending.pop(bus))
                self.assertFalse(pending)
                self.assertEqual(successful[:len(expected)], expected)
                # SW_RESET intentionally does not ACK, as NVIDIA documents in its driver.
                self.assertEqual(failed, [(0x3003, 1)])
                self.assertEqual((self.baseline / f'video{camera}.exit').read_text().strip(), '0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
