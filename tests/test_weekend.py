#!/usr/bin/env python3
"""Weekend ledger + weekend summary: the shapes both surfaces must agree on.

What is being protected here, and why each case exists:

  1. `session_kind` must not become a second classifier. Sprint Qualifying is not
     a Sprint -- that mistake already produced a wrong answer once (points.py's
     docstring). The mapping is checked kind by kind against points.session_type.
  2. The weekend key must group exactly one weekend. `meeting_key` is preferred,
     but the name+season fallback is what keeps old rows (and backfilled rows with
     no key) from splitting a weekend in half or merging two rounds.
  3. A card's state must come from capture evidence, never from the clock.
     Measured in the real archive: the Mexico City Race row had three captured
     frames while its start_date was still a day in the future.
  4. A missing session must never become an invented one. The grid shows the gap;
     the briefing says "kayıt yok" and explicitly forbids reading it as "did not
     happen".
  5. The clean-lap median must refuse to be a pace when there is not enough data.
     The real FP1 of that weekend has one lap per driver, and the fastest of those
     is an out-lap -- a number that is real but means nothing.

Isolated from the real database: every case builds its own temp store.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import points  # noqa: E402
import store  # noqa: E402
import weekend  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


class _TempDB(unittest.TestCase):
    """A store of its own, so a test can never touch the real archive."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir, self._old_db = store.DATA_DIR, store.DB_PATH
        store.DATA_DIR = __import__("pathlib").Path(self._tmp.name)
        store.DB_PATH = os.path.join(self._tmp.name, "test.db")
        store.init_db()
        weekend.clear_cache()
        self._seq = 0

    def tearDown(self):
        store.DATA_DIR, store.DB_PATH = self._old_dir, self._old_db
        weekend.clear_cache()
        self._tmp.cleanup()

    def add(self, session, *, meeting="Test Grand Prix", year=2026, key=1, date="2026-05-01T12:00:00",
            path=None, final_status=None, last_seen=None, frames=0, laps=2, lap_secs=None):
        self._seq += 1
        rid = store.upsert_race(path or f"{session}-{self._seq}", meeting=meeting,
                                official=meeting, session=session, year=year,
                                start_date=date, end_date=date, meeting_key=key)
        if laps:
            # One clean lap per driver, plus whatever the test asked for.
            rows = []
            for num in ("1", "11"):
                for i in range(laps):
                    sec = (lap_secs or {}).get(num, 80.0) + i * 0.5
                    rows.append((num, 3 + i, sec))
            store.put_lap_times(rid, rows)
        for lap in range(1, frames + 1):
            store.put_frame(rid, lap, {"rows": [{"num": "1", "code": "AAA", "pos": 1},
                                                {"num": "11", "code": "BBB", "pos": 2}]})
        if final_status or last_seen:
            with store.db() as c:
                c.execute("UPDATE races SET final_status=?, last_seen=? WHERE id=?",
                          (final_status, last_seen or store.now_iso(), rid))
        return rid


class SessionKindTest(unittest.TestCase):
    """kind mapping must agree with points.session_type, which is the one source."""

    def test_matches_points_for_known_sessions(self):
        for name, kind in (("Practice 1", "FP1"), ("Practice 2", "FP2"),
                           ("Practice 3", "FP3"), ("Qualifying", "Q"),
                           ("Sprint Qualifying", "SQ"), ("Sprint Shootout", "SQ"),
                           ("Sprint", "SP"), ("Race", "RACE")):
            self.assertEqual(store.session_kind(name), kind, name)

    def test_sprint_is_not_sprint_qualifying(self):
        self.assertNotEqual(store.session_kind("Sprint"), store.session_kind("Sprint Qualifying"))
        self.assertEqual(points.session_type("Sprint")["kind"], points.KIND_SPRINT)
        self.assertEqual(points.session_type("Sprint Qualifying")["kind"],
                         points.KIND_SPRINT_QUALIFYING)

    def test_unknown_and_unnumbered_practice_are_explicit(self):
        self.assertIsNone(store.session_kind(""))
        self.assertIsNone(store.session_kind("Something New"))
        # An unnumbered practice must not be assumed to be FP1: that would make
        # a real FP1 row unreachable and hide a session instead of naming it.
        self.assertEqual(store.session_kind("Practice"), "FP?")

    def test_card_label_is_shorter_than_the_briefing_label(self):
        """A card gets ~168 px; the briefing gets a sentence.

        Measured on a phone: "Antrenman 1" wrapped onto two lines on every
        practice card and the state badge ended up beside a broken label. The
        two labels therefore come from two tables, and this test is what keeps
        the short one actually short.
        """
        for kind in store.KIND_ORDER:
            short = store.card_label(kind)
            long = store.kind_label(kind)
            self.assertTrue(short, kind)
            self.assertLessEqual(len(short), len(long), f"{kind}: kısa etiket daha uzun")
            self.assertLessEqual(len(short), 10, f"{kind}: '{short}' karta sığmaz")
        # Unknown kinds fall back to something readable rather than a bare code.
        self.assertEqual(store.card_label("FP?"), store.kind_label("FP?"))

    def test_card_order_is_a_weekend_order(self):
        self.assertLess(store.KIND_ORDER.index("FP1"), store.KIND_ORDER.index("Q"))
        self.assertLess(store.KIND_ORDER.index("Q"), store.KIND_ORDER.index("RACE"))
        # A sprint weekend replaces FP2/FP3 with SQ/SP and keeps FP1/Q/RACE.
        self.assertNotIn("FP2", store.SPRINT_KINDS)
        self.assertIn("SQ", store.SPRINT_KINDS)
        self.assertIn("RACE", store.SPRINT_KINDS)


class WeekendKeyTest(_TempDB):
    def test_meeting_key_groups_the_weekend(self):
        a = self.add("Practice 1", key=42)
        b = self.add("Qualifying", key=42)
        c = self.add("Race", key=43, meeting="Other Grand Prix")
        wk = store.weekend_of(a)
        self.assertEqual([s["id"] for s in wk["sessions"]], [a, b])
        self.assertNotIn(c, [s["id"] for s in wk["sessions"]])

    def test_rows_without_meeting_key_group_by_season_and_name(self):
        a = self.add("Practice 1", key=None)
        b = self.add("Practice 2", key=None)
        self.assertEqual(store.weekend_key(store.get_race(a)),
                         store.weekend_key(store.get_race(b)))
        self.assertEqual(len(store.weekend_of(a)["sessions"]), 2)

    def test_same_name_different_season_is_a_different_weekend(self):
        a = self.add("Race", key=None, year=2026)
        b = self.add("Race", key=None, year=2025)
        self.assertNotEqual(store.weekend_key(store.get_race(a)),
                            store.weekend_key(store.get_race(b)))

    def test_sessions_come_back_in_weekend_order(self):
        self.add("Race", key=7, date="2026-05-03T14:00:00")
        self.add("Practice 1", key=7, date="2026-05-01T12:00:00")
        self.add("Qualifying", key=7, date="2026-05-02T15:00:00")
        rid = self.add("Practice 2", key=7, date="2026-05-01T16:00:00")
        got = [store.session_kind(s["session"]) for s in store.weekend_of(rid)["sessions"]]
        self.assertEqual(got, ["FP1", "FP2", "Q", "RACE"])

    def test_weekend_start_is_the_earliest_session_not_the_race(self):
        self.add("Race", key=9, date="2026-05-03T14:00:00")
        rid = self.add("Practice 1", key=9, date="2026-05-01T12:00:00")
        self.assertEqual(store.weekend_of(rid)["start_date"], "2026-05-01T12:00:00")


class SessionStateTest(unittest.TestCase):
    """State from evidence. Each case is a state the grid renders differently."""

    def _row(self, **kw):
        base = {"n_frames": 0, "n_lap_times": 0, "final_status": None,
                "last_seen": None, "start_date": "2026-05-01T12:00:00",
                "total_laps": None, "session": "Race"}
        base.update(kw)
        return base

    def test_no_row_is_missing_not_empty(self):
        self.assertEqual(weekend.session_state(None), "missing")

    def test_fresh_row_is_live(self):
        row = self._row(n_frames=3, last_seen=store.now_iso())
        self.assertEqual(weekend.session_state(row), "live")

    def test_finished_status_beats_freshness(self):
        # A session that just ended still has a fresh last_seen; it must not keep
        # a LIVE badge until the window expires.
        row = self._row(n_frames=3, last_seen=store.now_iso(), final_status="Ends")
        self.assertEqual(weekend.session_state(row), "done")

    def test_stale_row_with_data_is_done(self):
        old = (_now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(weekend.session_state(self._row(n_frames=2, last_seen=old)), "done")

    def test_future_start_without_data_is_scheduled(self):
        future = (_now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(weekend.session_state(self._row(start_date=future)), "scheduled")

    def test_future_start_WITH_stray_data_is_still_not_started(self):
        """Reversed after the user reported it on real data.

        This test used to assert the opposite ("frames prove it happened, so it
        is done"), on the assumption that any capture implies the session ran.
        The archive disproved it: the Mexico City race row held three frames
        while its start time was still ~a day away, and the grid showed the race
        as "bitti" (finished) before it had begun. Those frames were captured
        under the wrong session identity, not evidence that the race ran.

        A capture that stopped before the session could possibly have started
        therefore wins over stray frame counts. The bound is `_EARLIEST_HRS`:
        StartDate is track-local, so the earliest instant the session could have
        begun is start_local - 14 h (the largest real UTC offset).
        """
        future = (_now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self._row(n_frames=3, start_date=future,
                        last_seen=(_now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(weekend.session_state(row, max_lap=3), "scheduled")
        info = weekend.race_phase(row, max_lap=3)
        self.assertEqual(info["phase"], "not_started")
        self.assertFalse(info["event_over"])

    def test_verdict_does_not_drift_as_time_passes(self):
        """The same unchanged row must give the same answer forever.

        This is the bug the user hit twice. The first implementation compared
        `start_date` against `now` with a symmetric 12-hour margin to absorb the
        track's timezone. As the clock advanced, the margin swallowed the start
        time and the verdict moved with it -- with no new evidence at all:

            now+0h  -> başlamadı
            now+6h  -> tamamlandı     <-- the Mexico City race, reported as
            now+30h -> tamamlandı         "BİTTİ" before it had run

        A verdict that changes on its own is worse than no verdict: it makes the
        panel look like it is guessing, because it is. The rule now uses one-sided
        bounds and `last_seen` (stored UTC), so the answer is a function of the
        DATA, not of when the page happens to be opened.
        """
        # The real Mexico City race row, reproduced exactly.
        row = {"id": 21, "session": "Race", "total_laps": 56, "n_frames": 3,
               "n_lap_times": 43, "source": "live", "final_status": "Started",
               "start_date": "2026-11-01T14:00:00", "end_date": "2026-11-01T16:00:00",
               "last_seen": "2026-10-31T17:43:50Z"}
        base = datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc)
        seen = set()
        for hours in (0, 6, 12, 18, 24, 30, 48, 120, 24 * 365):
            now = base + timedelta(hours=hours)
            seen.add(weekend.session_state(row, max_lap=3, now=now))
        self.assertEqual(seen, {"scheduled"},
                         "karar zamana göre değişti: " + str(sorted(seen)))

    def test_stale_capture_after_the_latest_possible_end_is_finished(self):
        """The mirror image: a race we captured and then lost must not stay open.

        Same one-sided bounds, other end of the interval. A row whose latest
        possible end (`start + 12h + 6h`) is past is over whatever we recorded.
        """
        row = {"id": 1, "session": "Race", "total_laps": 58, "n_frames": 56,
               "n_lap_times": 1163, "source": "live", "final_status": "Started",
               "start_date": "2026-03-08T04:00:00", "last_seen": "2026-03-08T06:00:00Z"}
        info = weekend.race_phase(row, max_lap=57)
        self.assertEqual(info["phase"], "finished")
        self.assertFalse(info["record_complete"], "kayıt 57/58'de kesik")

    def test_unknown_is_its_own_state_not_finished(self):
        """`unknown` must never render as "tamamlandı".

        It did, and that is what put "BİTTİ" on a race that had not run.
        """
        self.assertEqual(weekend._STATE_LABELS["unknown"], "belirsiz")
        # A row with no usable clock evidence at all lands here.
        row = {"id": 9, "session": "Race", "total_laps": 56, "n_frames": 5,
               "n_lap_times": 90, "source": "live", "final_status": "Started",
               "start_date": None, "last_seen": None}
        self.assertEqual(weekend.session_state(row, max_lap=5), "unknown")

    def test_partial_record_is_finished_but_incomplete(self):
        """The Australian GP case: the race is over, our file stops at 57/58.

        The assistant told the user that race "has not finished yet, no
        chequered flag is visible" -- true of the file, false of the race. The
        two facts must be reported separately: `event_over` and
        `record_complete`.
        """
        past = (_now() - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (_now() - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self._row(n_frames=56, n_lap_times=1163, start_date=past, last_seen=old,
                        final_status="Started", total_laps=58, session="Race")
        info = weekend.race_phase(row, max_lap=57)
        self.assertEqual(info["phase"], "finished")
        self.assertTrue(info["event_over"], "yarış bitti")
        self.assertFalse(info["record_complete"], "kayıt 57/58'de kesik")
        # The card still renders it as a session that took place.
        self.assertEqual(weekend.session_state(row, max_lap=57), "done")

    def test_full_distance_record_is_complete(self):
        past = (_now() - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self._row(n_frames=56, n_lap_times=1163, start_date=past, last_seen=past,
                        final_status=None, total_laps=56, session="Race")
        info = weekend.race_phase(row, max_lap=56)
        self.assertEqual(info["phase"], "finished")
        self.assertTrue(info["record_complete"])

    def test_past_start_without_data_is_empty_not_missing(self):
        past = (_now() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(weekend.session_state(self._row(start_date=past)), "empty")


class OverviewTest(_TempDB):
    def test_missing_session_is_a_card_and_is_listed(self):
        self.add("Practice 1", key=5, frames=2)
        rid = self.add("Practice 2", key=5, frames=2)
        ov = weekend.overview(rid)
        kinds = [c["kind"] for c in ov["cards"]]
        self.assertEqual(kinds, ["FP1", "FP2", "FP3", "Q", "RACE"])
        self.assertEqual(ov["missing"], ["FP3", "Q", "RACE"])
        q = next(c for c in ov["cards"] if c["kind"] == "Q")
        self.assertIsNone(q["rid"], "a session with no row must carry no race id")
        self.assertEqual(q["best"], None)
        self.assertEqual(q["headline"], "", "an absent session shows no number")

    def test_sprint_weekend_shape_comes_from_capture(self):
        self.add("Practice 1", key=6, frames=2)
        self.add("Sprint Qualifying", key=6, frames=2)
        rid = self.add("Sprint", key=6, frames=2)
        ov = weekend.overview(rid)
        self.assertEqual(ov["shape_source"], "sessions")
        self.assertEqual([c["kind"] for c in ov["cards"]], list(store.SPRINT_KINDS))
        self.assertNotIn("FP2", [c["kind"] for c in ov["cards"]])

    def test_curated_sprint_round_switches_the_shape(self):
        self.add("Practice 1", key=8, year=2026, frames=2)
        rid = self.add("Practice 2", key=8, year=2026, frames=2)
        store.put_sprint_round(2026, 12)
        ov = weekend.overview(rid, round_no=12)
        self.assertEqual(ov["shape_source"], "calendar")
        self.assertIn("SQ", [c["kind"] for c in ov["cards"]])
        # Unknown round => normal shape, and the caller is told it is an assumption.
        ov2 = weekend.overview(rid, round_no=99)
        self.assertEqual(ov2["shape_source"], "default")
        self.assertEqual([c["kind"] for c in ov2["cards"]], list(store.NORMAL_KINDS))

    def test_live_card_is_marked_once(self):
        live = self.add("Practice 2", key=10, frames=3)
        with store.db() as c:
            c.execute("UPDATE races SET last_seen=?, final_status='Started' WHERE id=?",
                      (store.now_iso(), live))
        rid = self.add("Practice 1", key=10, frames=3, last_seen="2026-01-01T00:00:00Z",
                       final_status="Ends")
        ov = weekend.overview(rid)
        self.assertEqual(ov["live"], "FP2")
        self.assertEqual([c["live"] for c in ov["cards"]].count(True), 1)

    def test_unexpected_session_is_appended_not_dropped(self):
        rid = self.add("Practice 1", key=11, frames=2)
        extra = store.upsert_race("weird", meeting="Test Grand Prix", session="Practice 4",
                                  year=2026, start_date="2026-05-01T09:00:00", meeting_key=11)
        store.put_frame(extra, 1, {"rows": [{"num": "1", "pos": 1}]})
        ov = weekend.overview(rid)
        kinds = [c["kind"] for c in ov["cards"]]
        self.assertIn("FP?", kinds, "a stored session must not be hidden")
        self.assertIn("beklenmeyen", next(c for c in ov["cards"] if c["kind"] == "FP?")["label"])


class CleanRunTest(unittest.TestCase):
    def test_out_laps_are_not_pace(self):
        # 80.0 best, then a 95 s out-lap: the median must stay near race pace.
        times = {1: 80.0, 2: 80.4, 3: 80.2, 4: 95.0}
        med, n = weekend.clean_run(times)
        self.assertIsNotNone(med)
        self.assertLess(med, 81.0)
        self.assertEqual(n, 3, "the slow lap must be excluded, not averaged in")

    def test_too_few_clean_laps_returns_no_pace(self):
        med, n = weekend.clean_run({1: 80.0, 2: 140.0})
        self.assertIsNone(med, "one clean lap is not a long-run median")
        self.assertEqual(n, 1)

    def test_empty_and_missing_are_not_zero(self):
        self.assertEqual(weekend.clean_run({}), (None, 0))
        self.assertEqual(weekend.clean_run(None), (None, 0))


class SummaryBlockTest(_TempDB):
    def test_header_states_what_is_missing_and_forbids_inventing_it(self):
        self.add("Practice 1", key=12, frames=2)
        rid = self.add("Practice 2", key=12, frames=2)
        blk = weekend.summary_block(rid)
        self.assertIn("HAFTA SONU ÖZETİ", blk)
        self.assertIn("KAYIT YOK", blk)
        self.assertIn("koşulmadı", blk, "the block must prescribe the honest wording")
        self.assertIn("FP1", blk)
        self.assertIn("FP2", blk)

    def test_one_line_per_driver_and_no_duplicate_rows(self):
        rid = self.add("Practice 2", key=13, frames=2, laps=6)
        blk = weekend.summary_block(rid)
        body = [ln for ln in blk.splitlines() if " | " in ln and not ln.startswith("SÜRÜCÜ")]
        codes = [ln.split(" | ")[0] for ln in body]
        self.assertEqual(len(codes), len(set(codes)), "a driver appears twice")

    def test_nothing_captured_says_so_instead_of_crashing(self):
        rid = self.add("Practice 1", key=14, frames=0, laps=0)
        blk = weekend.summary_block(rid)
        self.assertIn("tur süresi", blk)

    def test_thin_session_is_labelled(self):
        # One lap per driver: the block must warn rather than present it as pace.
        rid = self.add("Practice 1", key=15, frames=1, laps=1)
        blk = weekend.summary_block(rid)
        self.assertIn("AZ KAYIT", blk)

    def test_result_is_memoised_but_refreshes_when_the_session_grows(self):
        rid = self.add("Practice 2", key=16, frames=1, laps=3)
        first = weekend.summary_block(rid)
        self.assertIs(first, weekend.summary_block(rid), "a repeated call must hit the cache")
        store.put_lap_times(rid, [("1", 40, 70.0), ("1", 41, 70.1)])
        second = weekend.summary_block(rid)
        self.assertNotEqual(first, second, "new laps must invalidate the cache")


class CardStatesBlockTest(_TempDB):
    """The chat briefing must quote the grid's states, never re-derive its own.

    Reported bug: a chat attached to the Race session called the captured
    Qualifying session `missing`, while `/api/weekend/<rid>` called the same card
    `done`. `missing` is the grid's word for "this weekend has no row for that
    session"; a sibling session that is merely outside the prompt's scope must not
    wear it.
    """

    def test_captured_sibling_session_is_done_not_missing(self):
        old = (_now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.add("Qualifying", key=21, frames=26, laps=3, final_status="Ends",
                 last_seen=old)
        rid = self.add("Race", key=21, frames=3, laps=3, final_status="Ends",
                       last_seen=old)
        blk = weekend.card_states_block(rid)
        ov = weekend.overview(rid)
        q = next(c for c in ov["cards"] if c["kind"] == "Q")
        self.assertEqual(q["state"], "done", "precondition: the card API says done")
        self.assertIn("Q (Sıralama) = done", blk)
        self.assertIn("26 kare", blk, "the capture count is the evidence for the state")
        self.assertIn("'missing' diye etiketleme", blk,
                      "the block must forbid mislabelling an out-of-scope session")
        self.assertIn("bu hafta sonunda o seansın hiç kaydı yok", blk,
                      "the meaning of `missing` must be stated, not assumed")
        # Every card is quoted exactly as the card API computes it: one producer,
        # so the chat and the grid cannot drift.
        for c in ov["cards"]:
            self.assertIn(f"{c['kind']} ({c['label']}) = {c['state']}", blk)

    def test_a_session_with_no_row_still_reads_missing(self):
        rid = self.add("Race", key=22, frames=2)
        blk = weekend.card_states_block(rid)
        self.assertIn("Q (Sıralama) = missing", blk,
                      "the honest use of `missing` must survive the fix")

    def test_unknown_race_returns_nothing_instead_of_raising(self):
        self.assertEqual(weekend.card_states_block(999999), "")


if __name__ == "__main__":
    unittest.main()


class BackfilledIdentityTest(_TempDB):
    """A restored session must not claim to be the race it was restored into.

    Measured on the real archive after the OpenF1 backfill: every practice frame
    carried `session: "Race"` because the frame builder hard-coded it, so the
    briefing said "seans_tipi=Yarış (race) | tur=26/26" for Practice 1 -- a
    26-lap grand prix that never happened. The race ROW is authoritative.
    """

    def test_backfilled_session_is_not_live(self):
        """`source=openf1` + a fresh `last_seen` must not read as "running now".

        `upsert_race` stamps last_seen with the moment of writing, so freshness
        alone made four restored practice sessions show a LIVE badge.
        """
        rid = self.add("Practice 1", key=77, frames=3, laps=6)
        with store.db() as c:
            c.execute("UPDATE races SET source=?, last_seen=?, end_date=? WHERE id=?",
                      (store.SOURCE_OPENF1, store.now_iso(), "2026-05-01T13:00:00", rid))
        row = store.get_race(rid)
        self.assertNotEqual(weekend.session_state(row, max_lap=6), "live")

    def test_backfilled_session_with_past_end_is_finished(self):
        rid = self.add("Practice 2", key=78, frames=3, laps=6)
        with store.db() as c:
            c.execute("UPDATE races SET source=?, last_seen=?, end_date=? WHERE id=?",
                      (store.SOURCE_OPENF1, store.now_iso(), "2026-05-01T13:00:00", rid))
        info = weekend.race_phase(store.get_race(rid), max_lap=6)
        self.assertEqual(info["phase"], "finished")

    def test_practice_has_no_race_distance_in_the_phase(self):
        rid = self.add("Practice 3", key=79, frames=3, laps=6)
        with store.db() as c:
            c.execute("UPDATE races SET source=?, end_date=? WHERE id=?",
                      (store.SOURCE_OPENF1, "2026-05-01T13:00:00", rid))
        info = weekend.race_phase(store.get_race(rid), max_lap=6)
        # A practice session's "last lap" is a session-clock tick, so a record
        # cannot be "complete" or "partial" against a race distance.
        self.assertTrue(info["event_over"])
        self.assertTrue(info["record_complete"])
