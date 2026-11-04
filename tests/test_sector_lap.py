#!/usr/bin/env python3
"""Sector rows must be filed under the lap they actually belong to.

The bug this locks down was found by reading the archive: every sector time in a
live-captured practice or qualifying session was stored against the WRONG lap --
one too high, for 275 of 286 laps in Mexico City qualifying.

Why it mattered: IDEAL and LOSS are computed from sector times, so every loss
number in those sessions was derived from a different lap than the one displayed.
The assistant noticed before we did and said so ("sektör verisi kendi içinde
tutarsız") -- a model reporting our bug as a data problem.

The rule now: the sector sum must equal a real lap time. A row that cannot be
verified is not stored, because a mislabelled sector silently corrupts three
derived numbers.

These tests drive the real selector with a fake feed state, no network and no
database -- the decision is pure arithmetic over what the feed published.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


class SectorLapTest(unittest.TestCase):
    def setUp(self):
        self._hist = dict(server.LAP_HISTORY)
        server.LAP_HISTORY.clear()
        server.LAP_HISTORY["s"] = {"16": {3: 698.514, 4: 77.532, 5: 98.229, 6: 91.4}}
        self._key = server._sess_key
        server._sess_key = lambda: "s"

    def tearDown(self):
        server._sess_key = self._key
        server.LAP_HISTORY.clear()
        server.LAP_HISTORY.update(self._hist)

    def _line(self, sectors, last="77.532", laps=4):
        return {"Sectors": [{"Value": v} for v in sectors],
                "LastLapTime": {"Value": last}, "NumberOfLaps": laps}

    def test_matching_counter_is_kept(self):
        got = server._sector_lap("16", self._line([30.0, 30.0, 17.532]), [30.0, 30.0, 17.532])
        self.assertEqual(got, 4)

    def test_off_by_one_counter_is_corrected(self):
        """The real case: sectors of lap 4 arrive labelled as lap 5."""
        vals = [30.0, 30.0, 17.532]           # sum 77.532 = lap 4
        got = server._sector_lap("16", self._line(vals, last="77.532", laps=5), vals)
        self.assertEqual(got, 4, "kaymış sayaç düzeltilmeli")

    def test_off_by_two_is_corrected(self):
        vals = [70.0, 100.0, 58.229]          # sum 228.229 — not a lap; use lap 5's
        vals = [40.0, 40.0, 18.229]           # sum 98.229 = lap 5
        got = server._sector_lap("16", self._line(vals, last="98.229", laps=7), vals)
        self.assertEqual(got, 5)

    def test_unverifiable_row_is_refused(self):
        """A complete triplet matching NO known lap must not be stored.

        This is the case that used to be written anyway, under whatever lap
        number the feed happened to be showing.
        """
        vals = [11.111, 22.222, 33.333]       # sum 66.666 — nothing like a lap
        got = server._sector_lap("16", self._line(vals, last="77.000", laps=4), vals)
        self.assertIsNone(got)

    def test_lap_time_evidence_beats_a_shifted_counter(self):
        """When the feed's own LastLapTime agrees with the counter, trust it.

        Not every off-by-one is a bug: the counter can legitimately match. What
        must never happen is storing a triplet whose sum disagrees with the lap
        time the feed itself published for that lap.
        """
        vals = [40.0, 40.0, 18.229]           # 98.229 = lap 5
        got = server._sector_lap("16", self._line(vals, last="98.229", laps=5), vals)
        self.assertEqual(got, 5)

    def test_partial_sectors_fall_back_to_the_counter(self):
        """Incomplete rows cannot be verified, so they keep the counter.

        They are harmless because put_sectors merges with COALESCE: a partial row
        can add a value but cannot overwrite a verified one.
        """
        got = server._sector_lap("16", self._line([30.0, None, None], laps=4),
                                 [30.0, None, None])
        self.assertEqual(got, 4)

    def test_driver_with_no_history_yet_keeps_the_counter(self):
        """The first moments of a session have nothing to verify against.

        Dropping those rows would lose the opening laps of every session; the
        counter is used instead, and `put_sectors`' COALESCE merge means such a
        row can never overwrite a verified one written later.
        """
        vals = [30.0, 30.0, 17.532]
        self.assertEqual(
            server._sector_lap("99", self._line(vals, last="77.000", laps=4), vals), 4)


if __name__ == "__main__":
    unittest.main()


class ArchiveSectorAttachTest(unittest.TestCase):
    """Backfilled frames must get their sector columns like live ones do.

    `_decorate` read the lap from the snapshot BODY. An OpenF1 frame writes the
    lap number only into the `frames` table, leaving `snap["lap"]` empty, so the
    lap was 0, the sector join was skipped, and every restored session opened
    with empty S1/S2/S3 columns and no colours at all. Live frames set the field,
    which is why the bug stayed invisible until a restored session was opened.
    """

    def test_lap_comes_from_the_frame_not_the_body(self):
        import tempfile, pathlib
        import store, analysis, weekend
        old = (store.DATA_DIR, store.DB_PATH)
        tmp = tempfile.TemporaryDirectory()
        store.DATA_DIR = pathlib.Path(tmp.name)
        store.DB_PATH = os.path.join(tmp.name, "s2.db")
        store.init_db()
        weekend.clear_cache()
        try:
            rid = store.upsert_race("q", meeting="X", session="Qualifying", year=2026,
                                    start_date="2026-05-01T12:00:00", meeting_key=9)
            # A frame with NO "lap" in its body -- exactly what the mirror writes.
            store.put_frame(rid, 7, {"rows": [{"num": "1", "code": "AAA", "pos": 1}]},
                            source=store.SOURCE_OPENF1)
            store.put_lap_times(rid, [("1", 7, 80.0)])
            store.put_sectors(rid, [("1", 7, 26.0, 30.0, 24.0, None, None, None)],
                              source=store.SOURCE_OPENF1)
            snap = analysis.archived_snapshot(rid, 7)
            self.assertEqual(snap.get("lap"), 7, "tur numarası çerçeveden gelmeli")
            self.assertTrue(snap.get("sector_lap"), "sektörler bağlanmalı")
            row = snap["rows"][0]
            self.assertAlmostEqual(row["s1"], 26.0)
            self.assertTrue(row.get("s_status"), "renk durumu hesaplanmalı")
        finally:
            store.DATA_DIR, store.DB_PATH = old
            weekend.clear_cache()
            tmp.cleanup()


class BestLapSectorTest(unittest.TestCase):
    """Best-lap sectors must travel with the row, and must not eat IDEAL/LOSS.

    Two separate defects, both found by looking at the finished qualifying screen:

      1. Every sector was yellow and no purple was possible. Reason: the sectors
         on screen belong to the lap in progress (or the last one), and once a
         session ends the last lap is an in-lap. Measured: field best 26.768 while
         every visible S1 read 31-44, so nothing could ever be purple.
      2. Adding the best-lap sectors introduced an early `continue` that skipped
         the IDEAL/LOSS computation, blanking both columns in every session.
    """

    def test_best_lap_sectors_are_returned_with_their_lap_number(self):
        import tempfile, pathlib
        import store, analysis, weekend
        old = (store.DATA_DIR, store.DB_PATH)
        tmp = tempfile.TemporaryDirectory()
        store.DATA_DIR = pathlib.Path(tmp.name)
        store.DB_PATH = os.path.join(tmp.name, "b.db")
        store.init_db()
        weekend.clear_cache()
        try:
            rid = store.upsert_race("q2", meeting="X", session="Qualifying", year=2026,
                                    start_date="2026-05-01T12:00:00", meeting_key=11)
            # `last` must be a real lap time: LOSS = last - ideal, and with an
            # empty last the calculation is correctly skipped.
            store.put_frame(rid, 3, {"rows": [{"num": "1", "code": "AAA", "pos": 1,
                                               "last": "1:25.000", "best": "1:20.000"}]})
            # lap 1 slow, lap 2 fast
            store.put_lap_times(rid, [("1", 1, 85.0), ("1", 2, 80.0)])
            store.put_sectors(rid, [("1", 1, 28.0, 32.0, 25.0, None, None, None),
                                    ("1", 2, 26.0, 30.0, 24.0, None, None, None)])
            bl = analysis.best_lap_sectors(rid)
            self.assertEqual(bl["laps"]["1"], 2, "en hızlı tur seçilmeli")
            self.assertEqual(bl["sectors"]["1"][2], [26.0, 30.0, 24.0])

            snap = analysis.archived_snapshot(rid, 3)
            row = snap["rows"][0]
            self.assertEqual(row.get("b_lap"), 2)
            self.assertEqual((row.get("bs1"), row.get("bs2"), row.get("bs3")),
                             (26.0, 30.0, 24.0))
            self.assertTrue(row.get("b_status"), "en iyi tur sektörleri renklenmeli")
            # The regression: IDEAL/LOSS must survive the best-lap block.
            self.assertIsNotNone(row.get("ideal"), "IDEAL boş kalmamalı")
            self.assertIsNotNone(row.get("loss"), "LOSS boş kalmamalı")
        finally:
            store.DATA_DIR, store.DB_PATH = old
            weekend.clear_cache()
            tmp.cleanup()

    def test_lap_without_sectors_is_never_chosen_as_best(self):
        """A driver's quickest time can sit on a lap whose sectors never came."""
        import tempfile, pathlib
        import store, analysis, weekend
        old = (store.DATA_DIR, store.DB_PATH)
        tmp = tempfile.TemporaryDirectory()
        store.DATA_DIR = pathlib.Path(tmp.name)
        store.DB_PATH = os.path.join(tmp.name, "c.db")
        store.init_db()
        weekend.clear_cache()
        try:
            rid = store.upsert_race("q3", meeting="X", session="Qualifying", year=2026,
                                    start_date="2026-05-01T12:00:00", meeting_key=12)
            store.put_lap_times(rid, [("1", 1, 80.0), ("1", 2, 79.0)])
            # Only the slower lap has sectors.
            store.put_sectors(rid, [("1", 1, 26.0, 30.0, 24.0, None, None, None)])
            bl = analysis.best_lap_sectors(rid)
            self.assertEqual(bl["laps"]["1"], 1,
                             "sektörü olmayan tur en iyi diye seçilmemeli")
        finally:
            store.DATA_DIR, store.DB_PATH = old
            weekend.clear_cache()
            tmp.cleanup()


class SectorSourceUiTest(unittest.TestCase):
    """The UI must default to the source that can actually show a purple."""

    def test_auto_source_follows_session_state(self):
        html = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "index.html"), encoding="utf-8").read()
        # Running -> last lap; finished -> best lap. A literal in the page is the
        # wrong place for this, so the decision lives in one function.
        self.assertIn("function sectorAuto(s)", html)
        self.assertIn('return running ? "last" : "best"', html)
        self.assertIn("function sectorUse(s)", html)
