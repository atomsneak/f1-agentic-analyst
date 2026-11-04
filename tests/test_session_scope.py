#!/usr/bin/env python3
"""Regression tests for the two bugs that made a practice session read as a race.

Measured damage (2026-10-30, real database): the Mexico Practice 1 race row held
1258 lap rows, of which 1163 were byte-identical to the United States Grand Prix
race and only 95 were real P1 laps. The briefing therefore showed a complete
56-lap, three-stint grand prix inside a practice session, and the model answered
"LEC attı 56 tur" for a driver with 29 laps -- correctly, from the data it was
given.

Two independent causes, one test each:

  1. LAP_HISTORY / STINTS were keyed by driver number alone, so a session change
     inherited the previous session's laps.
  2. A practice session has no race distance; its lap counter is a clock tick, so
     `lap=56/56` was presented as a finished 56-lap race.

Both are structural, so the tests assert the structure, not a rendered answer.
No database and no network are needed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import points      # noqa: E402
import analysis    # noqa: E402
import server      # noqa: E402


def _session(path, name, type_=None):
    """Minimal SessionInfo payload the way the feed sends it."""
    return {"Path": path, "Name": name, "Type": type_ or name,
            "Meeting": {"Name": "Test GP", "Year": 2026}}


class SessionScopedCache(unittest.TestCase):
    """Cause 1: the lap/stint caches must not survive a session change."""

    def setUp(self):
        server.LAP_HISTORY.clear()
        server.STINTS.clear()
        server.STATE.clear()
        server._arch_state.update({"lap": None, "t": 0.0})

    def tearDown(self):
        server.LAP_HISTORY.clear()
        server.STINTS.clear()
        server.STATE.clear()

    def _laps(self, session_path):
        return server.LAP_HISTORY.get(session_path) or {}

    def test_race_laps_do_not_leak_into_next_session(self):
        # A race: driver 16 completes 50 laps.
        server.STATE["SessionInfo"] = _session("2026/x/Race/", "Race")
        server.STATE["TimingData"] = {"Lines": {"16": {
            "NumberOfLaps": 50, "LastLapTime": {"Value": "1:38.000"}}}}
        server._record_laps()
        self.assertEqual(self._laps("2026/x/Race/")["16"][50], 98.0)

        # Next session on the same socket: a practice. Driver 16 has done 3 laps.
        server.STATE["SessionInfo"] = _session("2026/x/Practice 1/", "Practice 1")
        server.STATE["TimingData"] = {"Lines": {"16": {
            "NumberOfLaps": 3, "LastLapTime": {"Value": "1:40.000"}}}}
        server._record_laps()

        practice = self._laps("2026/x/Practice 1/")
        self.assertEqual(sorted(practice["16"]), [3],
                         "practice inherited laps from the race session")
        # And the race's own history must still be intact, not moved or erased.
        self.assertEqual(sorted(self._laps("2026/x/Race/")["16"]), [50])

    def test_stints_are_session_scoped(self):
        server.STATE["SessionInfo"] = _session("2026/x/Race/", "Race")
        server._track_stints({"Lines": {"16": {"Stints": [
            {"Compound": "SOFT", "TotalLaps": 15},
            {"Compound": "HARD", "TotalLaps": 40}]}}})
        self.assertEqual(len(server._stints("16")), 2)

        server.STATE["SessionInfo"] = _session("2026/x/Practice 1/", "Practice 1")
        self.assertEqual(server._stints("16"), [],
                         "practice inherited the race's stint compounds")

    def test_early_frames_are_migrated_when_identity_arrives(self):
        """The first frames of a connection can precede SessionInfo."""
        server.STATE["TimingData"] = {"Lines": {"16": {
            "NumberOfLaps": 2, "LastLapTime": {"Value": "1:39.000"}}}}
        server._record_laps()                      # keyed under "" (unknown)
        self.assertEqual(server._sess_key(), "")

        server._absorb_result({"SessionInfo": _session("2026/x/Practice 1/", "Practice 1")})
        self.assertEqual(sorted(self._laps("2026/x/Practice 1/")["16"]), [2])
        self.assertNotIn("", server.LAP_HISTORY)

    def test_archive_writes_only_current_session(self):
        """Write path: the frame writer must read one bucket, not all of them."""
        server.STATE["SessionInfo"] = _session("2026/x/Race/", "Race")
        server.LAP_HISTORY["2026/x/Race/"] = {"16": {1: 98.0, 2: 98.1}}
        server.LAP_HISTORY["2026/x/Practice 1/"] = {"16": {1: 105.0}}

        server.STATE["SessionInfo"] = _session("2026/x/Practice 1/", "Practice 1")
        fresh = []
        stored = set()
        for num, hist in (server.LAP_HISTORY.get(server._sess_key()) or {}).items():
            for ln, sec in hist.items():
                if (num, int(ln)) not in stored:
                    fresh.append((num, int(ln), sec))
                    stored.add((num, int(ln)))
        self.assertEqual(fresh, [("16", 1, 105.0)])


class SessionTaxonomy(unittest.TestCase):
    """Cause 2: a session must be classified, and its counter interpreted, by kind."""

    def test_practice_has_no_race_distance(self):
        t = points.session_type("Practice 1")
        self.assertEqual(t["kind"], points.KIND_PRACTICE)
        self.assertFalse(t["has_race_distance"])
        self.assertFalse(t["pays_points"])
        self.assertIn("antrenman", t["pace_label"])
        self.assertNotIn("yarış temposu", t["pace_label"])

    def test_race_and_sprint_have_race_distance(self):
        for name in ("Race", "Sprint"):
            t = points.session_type(name)
            self.assertTrue(t["has_race_distance"], name)
            self.assertTrue(t["pays_points"], name)

    def test_qualifying_segments(self):
        self.assertEqual(points.session_type("Qualifying")["segments"], ["Q1", "Q2", "Q3"])
        self.assertEqual(points.session_type("Sprint Qualifying")["segments"],
                         ["SQ1", "SQ2", "SQ3"])
        self.assertEqual(points.session_type("Sprint Shootout")["segments"],
                         ["SQ1", "SQ2", "SQ3"])

    def test_sprint_is_not_sprint_qualifying(self):
        """The distinction the user asked about explicitly."""
        sprint = points.session_type("Sprint")
        sq = points.session_type("Sprint Qualifying")
        self.assertEqual(sprint["kind"], points.KIND_SPRINT)
        self.assertEqual(sq["kind"], points.KIND_SPRINT_QUALIFYING)
        self.assertTrue(sprint["pays_points"])
        self.assertFalse(sq["pays_points"], "sprint qualifying awards no points")
        self.assertTrue(sq["is_qualifying"])
        self.assertFalse(sprint["is_qualifying"])
        self.assertNotEqual(sprint["pace_label"], sq["pace_label"])

    def test_classification_matches_old_points_behaviour(self):
        """The rewritten taxonomy must not change which sessions pay points."""
        for name, expected in (("Race", True), ("Sprint", True),
                               ("Sprint Qualifying", False), ("Sprint Shootout", False),
                               ("Qualifying", False), ("Practice 1", False), ("", False)):
            got = points.session_type(name)["pays_points"]
            self.assertEqual(got, expected, name)
            self.assertEqual(got, points.pays_points(name), name)


class BriefingStatesTheSession(unittest.TestCase):
    """The briefing line is what the model actually reads; assert it says the truth."""

    def _snap(self, name, **kw):
        snap = {"session": name, "session_type": points.session_type(name),
                "meeting": "Test GP", "status": "Inactive", "rows": [], "rc": [],
                "lap": 56, "total": 56, "track": "Green"}
        snap.update(kw)
        return snap

    def test_practice_counter_is_not_shown_as_race_distance(self):
        ctx = analysis.context_text(self._snap("Practice 1", field_laps=33,
                                               countdown="0:12:34"))
        head = ctx.splitlines()[0]
        self.assertNotIn("tur=56/56", head)
        self.assertIn("YARIŞ TURU DEĞİL", head)
        self.assertIn("sahada_en_çok_tur=33", head)

    def test_countdown_reaches_the_briefing(self):
        ctx = analysis.context_text(self._snap("Practice 1", countdown="0:12:34"))
        self.assertIn("KALAN SÜRE=0:12:34", ctx.splitlines()[0])

    def test_race_keeps_its_race_distance(self):
        ctx = analysis.context_text(self._snap("Race", total=56))
        self.assertIn("tur=56/56", ctx.splitlines()[0])
        self.assertNotIn("YARIŞ TURU DEĞİL", ctx.splitlines()[0])

    def test_rules_block_forbids_race_pace_in_practice(self):
        ctx = analysis.context_text(self._snap("Practice 1"))
        rules = next(l for l in ctx.splitlines() if l.startswith("SEANS KURALI"))
        self.assertIn("practice", rules)
        self.assertIn("YARIŞ MESAFESİ YOK", rules)
        self.assertIn("antrenman", rules)

    def test_rules_block_explains_qualifying_segments(self):
        ctx = analysis.context_text(self._snap("Qualifying", countdown="0:04:11"))
        rules = next(l for l in ctx.splitlines() if l.startswith("SEANS KURALI"))
        self.assertIn("Q1/Q2/Q3", rules)
        self.assertIn("0:04:11", rules)

    def test_driver_lap_count_rule_is_present(self):
        """The exact question that was answered wrong: 'LEC kaç tur attı'."""
        ctx = analysis.context_text(self._snap("Practice 1"))
        rules = next(l for l in ctx.splitlines() if l.startswith("SEANS KURALI"))
        self.assertIn("LAPS kolonundan", rules)


class SectorShapes(unittest.TestCase):
    """The live feed sends Sectors as a list OR an index-keyed dict.

    Slicing the dict raised `KeyError: slice(None, 3, None)` on every tick
    ("sektor eklenemedi" / "archive: frame write failed" in the journal), so live
    sector data never reached the briefing at all.
    """

    def setUp(self):
        server.STATE.clear()

    def tearDown(self):
        server.STATE.clear()

    def test_sector_list_accepts_both_shapes(self):
        as_list = [{"Value": "27.0"}, {"Value": "30.0"}, {"Value": "41.0"}]
        as_dict = {"0": {"Value": "27.0"}, "1": {"Value": "30.0"}, "2": {"Value": "41.0"}}
        self.assertEqual(analysis._sector_list(as_list), as_list)
        self.assertEqual(analysis._sector_list(as_dict), as_list,
                         "index-keyed dict must normalise in key order, not insertion order")
        self.assertEqual(analysis._sector_list(None), [])
        self.assertEqual(analysis._sector_list("nonsense"), [])

    def test_sector_rows_survive_a_dict_payload(self):
        server.STATE["TimingData"] = {"Lines": {"16": {"NumberOfLaps": 5, "Sectors": {
            "0": {"Value": "27.316", "Segments": [{"Status": 2048}]},
            "1": {"Value": "30.0", "Segments": [{"Status": 2051}]},
            "2": {"Value": "40.7", "Segments": [{"Status": 2049}]}}}}}
        rows = server._sector_rows()          # used to raise KeyError
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2:5], ("27.316", "30.0", "40.7"))

    def test_sectors_are_replaced_not_accumulated(self):
        """Sectors describe the last completed lap, not an append-only stream.

        Extending them put every earlier lap's splits in front of the current one,
        so secs[:3] read the OLDEST lap of the session.
        """
        server.STATE["TimingData"] = {"Lines": {"16": {"Sectors": [
            {"Value": "lap1-s1"}, {"Value": "lap1-s2"}, {"Value": "lap1-s3"}]}}}
        server._absorb_patch(["TimingData", {"Lines": {"16": {"Sectors": [
            {"Value": "lap2-s1"}, {"Value": "lap2-s2"}, {"Value": "lap2-s3"}]}}}])
        got = server.STATE["TimingData"]["Lines"]["16"]["Sectors"]
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["Value"], "lap2-s1")


if __name__ == "__main__":
    unittest.main()
