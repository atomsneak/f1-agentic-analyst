#!/usr/bin/env python3
"""Regression test: patch-shaped Segments must not kill the archive tick.

Measured damage (2026-10-31, live): `archive: frame write failed: 'str' object
has no attribute 'get'` was logged 754 times between 01:00 and 21:00, roughly
once per 20 s tick. `_sector_rows` built the mini-sector list with

    [g.get("Status") for g in (s.get("Segments") or [])]

The full snapshot sends Segments as a LIST of `{Status: 2048}`; patches send an
index-keyed DICT. Iterating a dict yields its keys, so `g` was a string and
`.get` did not exist.

Why one bad field cost the whole session: the tick writes lap times, sectors,
frame and race state inside ONE try block, so the exception also skipped
`put_frame`. Stored frames for the running race stopped advancing while the
timing page kept rendering from memory -- the archive was silently frozen.

No database, no network: server.STATE is stubbed.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

SEG = [{"Status": 2048}, {"Status": 2049}, {"Status": 2051}]


def _lines(**segments):
    """TimingData.Lines with one driver; caller decides the Segments shape."""
    return {"1": {"NumberOfLaps": 12,
                  "Sectors": [{"Value": "27.316", "Segments": segments.get("s1", SEG)},
                              {"Value": "39.402", "Segments": segments.get("s2", SEG)},
                              {"Value": "43.101", "Segments": segments.get("s3", SEG)}]}}


class SegStatuses(unittest.TestCase):
    def test_list_of_dicts(self):
        self.assertEqual(server._seg_statuses(SEG), [2048, 2049, 2051])

    def test_index_keyed_dict_does_not_raise(self):
        # The exact shape that produced the 754 failures.
        raw = {"1": {"Status": 2048}, "2": {"Status": 2051}, "0": {"Status": 2049}}
        self.assertEqual(server._seg_statuses(raw), [2049, 2048, 2051])

    def test_bare_codes(self):
        self.assertEqual(server._seg_statuses([2048, 2049]), [2048, 2049])

    def test_empty_and_missing(self):
        self.assertIsNone(server._seg_statuses(None))
        self.assertIsNone(server._seg_statuses([]))
        self.assertIsNone(server._seg_statuses({}))


class SectorRows(unittest.TestCase):
    def _rows(self, lines):
        with mock.patch.object(server, "STATE", {"TimingData": {"Lines": lines}}):
            return server._sector_rows()

    def test_full_snapshot_shape(self):
        rows = self._rows(_lines())
        self.assertEqual(rows, [("1", 12, "27.316", "39.402", "43.101",
                                 [2048, 2049, 2051], [2048, 2049, 2051],
                                 [2048, 2049, 2051])])

    def test_patch_shape_is_stored_the_same(self):
        dict_seg = {"0": {"Status": 2048}, "1": {"Status": 2049}, "2": {"Status": 2051}}
        rows = self._rows(_lines(s1=dict_seg))
        self.assertEqual(rows[0][5], [2048, 2049, 2051])

    def test_stopped_sector_reports_nothing(self):
        lines = {"1": {"NumberOfLaps": 12,
                       "Sectors": [{"Value": "27.316", "Segments": SEG},
                                   {"Stopped": True, "Segments": SEG},
                                   {"Value": None, "Segments": SEG}]}}
        rows = self._rows(lines)
        self.assertEqual(rows[0][2:5], ("27.316", None, None))
        self.assertIsNone(rows[0][6])


if __name__ == "__main__":
    unittest.main()
