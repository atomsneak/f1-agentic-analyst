#!/usr/bin/env python3
"""Regression test: a best lap with unpublished sectors must not crash the chat.

Measured damage (2026-10-31, Mexico GP race 21, live): `/api/chat` returned
HTTP 500 for every question while the race was on lap 3. The traceback was

    analysis.best_lap_block -> f"{row['s2']:.3f}"
    TypeError: unsupported format string passed to NoneType.__format__

The feed had published S1 and S2 for the current best lap but not S3 yet (18 of
22 drivers), and `best_lap_block` guarded the whole triple with a single
`row.get("s1") is not None` check. One not-yet-published sector therefore took
down the entire briefing -- the assistant panel showed "İstek hatası: HTTP 500"
for the whole session.

The rule is the one the POS table already follows: a sector the feed never
published prints as "-", it is never interpolated or defaulted to 0.

No database, no network: store is stubbed.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis  # noqa: E402


def _stub(times, sectors, speeds=None, meta=None):
    """Store stubs: one driver's lap map, its sector row, its speeds, its code."""
    speeds = speeds or {}
    meta = meta or {}

    def sectors_for(rid, lap):
        return {k: v for k, v in sectors.items() if v is not None}, lap

    return (
        mock.patch.object(analysis.store, "all_lap_times", lambda rid: times),
        mock.patch.object(analysis.store, "sectors_for", sectors_for),
        mock.patch.object(analysis.store, "speeds_for",
                          lambda rid, lap: speeds.get(lap, {})),
        mock.patch.object(analysis.store, "race_driver_meta", lambda rid: meta),
    )


class BestLapSectorFormat(unittest.TestCase):
    def _block(self, sectors, speeds=None):
        patches = _stub({"1": {2: 110.22}}, {"1": sectors}, speeds,
                        {"1": {"code": "NOR"}})
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return analysis.best_lap_block({"race_id": 21})

    def test_all_three_sectors(self):
        bl = self._block({"s1": 27.304, "s2": 39.606, "s3": 43.31})
        self.assertIn("S=27.304/39.606/43.310", bl[-1])

    def test_last_sector_not_published_yet(self):
        # The exact live shape that produced the 500.
        bl = self._block({"s1": 27.304, "s2": 39.606, "s3": None})
        self.assertIn("S=27.304/39.606/-", bl[-1])
        self.assertNotIn("None", bl[-1])

    def test_only_first_sector_published(self):
        bl = self._block({"s1": 27.304, "s2": None, "s3": None})
        self.assertIn("S=27.304/-/-", bl[-1])

    def test_no_sector_at_all_prints_no_sector_field(self):
        bl = self._block({"s1": None, "s2": None, "s3": None})
        self.assertNotIn("S=", bl[-1])

    def test_speeds_with_missing_boundaries(self):
        bl = self._block({"s1": 27.3, "s2": 39.6, "s3": 43.3},
                         {2: {"1": {"i1": 312, "i2": None, "st": None}}})
        self.assertIn("i1/i2/st=312/-/-", bl[-1])


class AllLapTimesNullGuard(unittest.TestCase):
    def test_null_duration_row_is_skipped(self):
        """A lap row without a duration is not a lap; it must not raise."""
        rows = [{"num": "1", "lap": 2, "sec": 110.2},
                {"num": "1", "lap": 3, "sec": None}]
        with mock.patch.object(analysis.store, "db") as db:
            conn = mock.MagicMock()
            conn.__enter__.return_value = conn
            conn.execute.return_value.fetchall.return_value = rows
            db.return_value = conn
            out = analysis.store.all_lap_times(21)
        self.assertEqual(out, {"1": {2: 110.2}})


if __name__ == "__main__":
    unittest.main()
