#!/usr/bin/env python3
"""Unit tests for points.py -- the championship arithmetic.

Run:  .venv/bin/python -m unittest discover -s tests -v

These are the numbers the live panel shows during a race, so the cases below are
the ones that would hurt if they were wrong: an off-by-one in the table (P3
scoring 12 instead of 15), a retired driver scoring, and a projection that
silently drops a driver who is not in the cached base table.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import points  # noqa: E402


class TestTables(unittest.TestCase):
    def test_race_table_is_the_real_one(self):
        self.assertEqual(points.RACE_POINTS, (25, 18, 15, 12, 10, 8, 6, 4, 2, 1))
        self.assertEqual(points.SPRINT_POINTS, (8, 7, 6, 5, 4, 3, 2, 1))

    def test_every_race_position(self):
        expect = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
        for pos, pts in expect.items():
            self.assertEqual(points.points_for(pos), pts, f"P{pos}")
        for pos in (11, 12, 20, 27):
            self.assertEqual(points.points_for(pos), 0, f"P{pos} puan almamalı")

    def test_every_sprint_position(self):
        expect = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}
        for pos, pts in expect.items():
            self.assertEqual(points.points_for(pos, "sprint"), pts, f"sprint P{pos}")
        for pos in (9, 10, 20):
            self.assertEqual(points.points_for(pos, "sprint"), 0)

    def test_no_fastest_lap_point_exists(self):
        # 2025 sonrası en hızlı tur puanı yok: tabloda 11. sırada puan olmamalı.
        self.assertEqual(points.points_for(11), 0)
        self.assertEqual(len(points.RACE_POINTS), 10)
        self.assertEqual(len(points.SPRINT_POINTS), 8)

    def test_nonsense_positions_score_zero(self):
        for v in (0, -1, None, "", "DNF", "P3", [], {}):
            self.assertEqual(points.points_for(v), 0, repr(v))

    def test_numeric_string_is_accepted(self):
        self.assertEqual(points.points_for("1"), 25)
        self.assertEqual(points.points_for(3.0), 15)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            points.points_for(1, "practice")
        with self.assertRaises(ValueError):
            points.table("qualifying")


class TestSessionKind(unittest.TestCase):
    def test_session_names(self):
        self.assertEqual(points.session_kind("Sprint"), "sprint")
        self.assertEqual(points.session_kind("Sprint Qualifying"), "sprint")
        self.assertEqual(points.session_kind("Race"), "race")
        self.assertEqual(points.session_kind("Practice 1"), "race")
        self.assertTrue(points.pays_points("Race"))
        self.assertTrue(points.pays_points("Sprint"))
        self.assertFalse(points.pays_points("Sprint Qualifying"))
        self.assertFalse(points.pays_points("Sprint Shootout"))
        self.assertFalse(points.pays_points("Qualifying"))
        self.assertFalse(points.pays_points("Practice 2"))
        self.assertFalse(points.pays_points(""))


class TestAwardForOrder(unittest.TestCase):
    def test_order_maps_to_points(self):
        order = ["VER", "NOR", "LEC", "PIA", "SAI", "HAM", "RUS", "PER", "ALO", "TSU", "HUL"]
        a = points.award_for_order(order)
        self.assertEqual(a["VER"], 25)
        self.assertEqual(a["NOR"], 18)
        self.assertEqual(a["TSU"], 1)
        self.assertEqual(a["HUL"], 0)
        self.assertEqual(sum(a.values()), 101)          # 25+18+15+12+10+8+6+4+2+1
        self.assertEqual(len(a), 11)

    def test_sprint_order(self):
        a = points.award_for_order(["A", "B", "C"], "sprint")
        self.assertEqual((a["A"], a["B"], a["C"]), (8, 7, 6))


def _base():
    """A tiny base table: leader, a chaser, a midfielder, a backmarker."""
    return [
        {"key": "VER", "label": "VER", "points": 362, "position": 1},
        {"key": "NOR", "label": "NOR", "points": 314, "position": 2},
        {"key": "LEC", "label": "LEC", "points": 288, "position": 3},
        {"key": "TSU", "label": "TSU", "points": 34, "position": 10},
    ]


class TestProject(unittest.TestCase):
    def test_deltas_and_totals(self):
        awards = {"VER": 25, "NOR": 18, "LEC": 15, "TSU": 0}
        r = points.project(_base(), awards)
        row = {x["key"]: x for x in r["rows"]}
        self.assertEqual(row["VER"]["base_points"], 362)
        self.assertEqual(row["VER"]["race_points"], 25)
        self.assertEqual(row["VER"]["total"], 387)
        self.assertEqual(row["NOR"]["total"], 332)
        self.assertEqual(row["TSU"]["total"], 34)
        self.assertEqual(points.gained(r["rows"]), 58)
        self.assertEqual(points.totals(r["rows"]), 362 + 314 + 288 + 34 + 58)

    def test_order_wins_follow_the_projection(self):
        # VER yarışı kaybeder, NOR kazanır: 314+25=339, 362+18=380 -> lider değişmez
        r = points.project(_base(), {"NOR": 25, "VER": 18})
        row = {x["key"]: x for x in r["rows"]}
        self.assertEqual(row["VER"]["rank"], 1)
        self.assertEqual(row["NOR"]["rank"], 2)
        self.assertEqual(row["NOR"]["rank_change"], 0)
        # Ama büyük farkla: NOR liderliği alır
        base = _base()
        base[1]["points"] = 360
        r = points.project(base, {"NOR": 25, "VER": 0})
        row = {x["key"]: x for x in r["rows"]}
        self.assertEqual(row["NOR"]["rank"], 1)
        self.assertEqual(row["VER"]["rank"], 2)
        self.assertEqual(row["NOR"]["rank_change"], 1)
        self.assertEqual(row["VER"]["rank_change"], -1)

    def test_no_award_keeps_the_base_order(self):
        r = points.project(_base(), {})
        self.assertEqual([x["key"] for x in r["rows"]], ["VER", "NOR", "LEC", "TSU"])
        for x in r["rows"]:
            self.assertEqual(x["race_points"], 0)
            self.assertEqual(x["total"], x["base_points"])

    def test_driver_missing_from_base_is_reported_not_invented(self):
        r = points.project(_base(), {"VER": 25, "NEW": 18})
        self.assertEqual(r["unmatched"], ["NEW"])
        self.assertNotIn("NEW", [x["key"] for x in r["rows"]])

    def test_ties_share_a_rank_and_are_flagged(self):
        base = [
            {"key": "A", "points": 100, "position": 1},
            {"key": "B", "points": 90, "position": 2},
            {"key": "C", "points": 89, "position": 3},
        ]
        r = points.project(base, {"B": 10, "C": 11})   # 100 / 100 / 100
        self.assertTrue(all(x["tie"] for x in r["rows"]))
        self.assertEqual({x["tie_rank"] for x in r["rows"]}, {1})
        self.assertEqual(r["tie_groups"], [["A", "B", "C"]])
        # Aynı puanda sıra resmî tie-break değil: taban sırası korunur (deterministik).
        self.assertEqual([x["key"] for x in r["rows"]], ["A", "B", "C"])

    def test_duplicate_base_rows_do_not_double_count(self):
        base = _base() + [{"key": "VER", "points": 362, "position": 1}]
        r = points.project(base, {"VER": 25})
        vers = [x for x in r["rows"] if x["key"] == "VER"]
        self.assertEqual(len(vers), 1)
        self.assertEqual(vers[0]["total"], 387)

    def test_bad_point_values_are_zero_not_crash(self):
        base = [{"key": "A", "points": "yok", "position": None}]
        r = points.project(base, {"A": None})
        self.assertEqual(r["rows"][0]["total"], 0)
        self.assertIsNone(r["rows"][0]["rank_change"])

    def test_full_field_projection_after_a_race(self):
        # Gerçek şekil: 22 sürücü, tam yarış puanları.
        base = [{"key": f"D{i:02d}", "points": 400 - i * 10, "position": i + 1}
                for i in range(22)]
        order = [f"D{i:02d}" for i in range(22)]
        r = points.project(base, points.award_for_order(order))
        self.assertEqual(len(r["rows"]), 22)
        self.assertEqual(points.gained(r["rows"]), 101)
        self.assertEqual(r["rows"][0]["key"], "D00")
        self.assertEqual(r["rows"][0]["total"], 400 + 25)
        # Sıralama monoton: her satırın toplamı bir sonrakinden küçük değil
        phases = [x["total"] for x in r["rows"]]
        self.assertEqual(phases, sorted(phases, reverse=True))


class TestRunningAwards(unittest.TestCase):
    def _rows(self, n=12, retired=()):
        rows = []
        for i in range(n):
            code = f"D{i:02d}"
            rows.append({"pos": i + 1, "code": code, "retired": code in retired})
        return rows

    def test_points_come_from_position(self):
        awards, scored = points.running_awards(self._rows(12))
        self.assertEqual(awards["D00"], 25)
        self.assertEqual(awards["D09"], 1)
        self.assertNotIn("D10", awards)          # P11 puan almaz
        self.assertEqual(sum(awards.values()), 101)
        self.assertEqual(len(scored), 10)

    def test_retired_driver_scores_nothing(self):
        awards, _ = points.running_awards(self._rows(12, retired=("D00",)))
        self.assertNotIn("D00", awards)
        self.assertEqual(sum(awards.values()), 76)   # 18+15+12+10+8+6+4+2+1

    def test_unpositioned_rows_are_skipped(self):
        rows = [{"pos": 1, "code": "A"}, {"pos": 99, "code": "B"},
                {"pos": None, "code": "C"}, {"pos": 2, "code": "D"}]
        awards, _ = points.running_awards(rows)
        self.assertEqual(awards, {"A": 25, "D": 18})

    def test_sprint_table_applies(self):
        awards, _ = points.running_awards(self._rows(10), "sprint")
        self.assertEqual(awards["D00"], 8)
        self.assertEqual(awards["D07"], 1)
        self.assertNotIn("D08", awards)
        self.assertEqual(sum(awards.values()), 36)


class TestProvisional(unittest.TestCase):
    BASE_D = [
        {"key": "VER", "code": "VER", "name": "Max", "team": "Red Bull", "team_id": "red_bull",
         "position": 1, "points": 362, "wins": 11},
        {"key": "NOR", "code": "NOR", "name": "Lando", "team": "McLaren", "team_id": "mclaren",
         "position": 2, "points": 314, "wins": 4},
        {"key": "LEC", "code": "LEC", "name": "Charles", "team": "Ferrari", "team_id": "ferrari",
         "position": 3, "points": 288, "wins": 2},
    ]
    BASE_C = [
        {"key": "red_bull", "name": "Red Bull", "position": 1, "points": 514},
        {"key": "mclaren", "name": "McLaren", "position": 2, "points": 559},
        {"key": "ferrari", "name": "Ferrari", "position": 3, "points": 468},
    ]

    def test_running_order_reorders_the_table(self):
        race = [{"pos": 1, "code": "NOR"}, {"pos": 2, "code": "VER"}, {"pos": 3, "code": "LEC"}]
        p = points.provisional(race, self.BASE_D, self.BASE_C, lap=30, total_laps=58)
        d = {x["key"]: x for x in p["drivers"]}
        self.assertEqual(d["VER"]["race_points"], 18)
        self.assertEqual(d["VER"]["total"], 380)
        self.assertEqual(d["NOR"]["race_points"], 25)
        self.assertEqual(d["NOR"]["total"], 339)
        self.assertEqual(p["drivers"][0]["key"], "VER")
        self.assertEqual(d["VER"]["rank_change"], 0)
        self.assertTrue(p["provisional"])
        self.assertEqual(p["awarded"], 58)

    def test_a_lead_change_shows_as_rank_change(self):
        base = [dict(r) for r in self.BASE_D]
        base[1]["points"] = 360                     # NOR liderliğe çok yakın
        race = [{"pos": 1, "code": "NOR"}, {"pos": 2, "code": "VER"}]
        p = points.provisional(race, base, self.BASE_C)
        d = {x["key"]: x for x in p["drivers"]}
        self.assertEqual(p["drivers"][0]["key"], "NOR")
        self.assertEqual(d["NOR"]["rank_change"], 1)   # 2. -> 1.
        self.assertEqual(d["VER"]["rank_change"], -1)  # 1. -> 2.

    def test_constructors_add_up_driver_points(self):
        race = [{"pos": 1, "code": "NOR"}, {"pos": 2, "code": "VER"}]
        p = points.provisional(race, self.BASE_D, self.BASE_C)
        c = {x["key"]: x for x in p["constructors"]}
        self.assertEqual(c["mclaren"]["race_points"], 25)
        self.assertEqual(c["mclaren"]["total"], 559 + 25)
        self.assertEqual(c["red_bull"]["race_points"], 18)
        self.assertEqual(c["red_bull"]["total"], 514 + 18)
        self.assertEqual(c["ferrari"]["race_points"], 0)

    def test_driver_not_in_base_is_reported(self):
        race = [{"pos": 1, "code": "NEW"}]
        p = points.provisional(race, self.BASE_D, self.BASE_C)
        self.assertEqual(p["unmatched"], ["NEW"])
        self.assertEqual(p["unmapped_teams"], ["NEW"])
        self.assertEqual(p["awarded"], 25)          # puan verildi ama tabloya girmedi

    def test_empty_race_rows_leave_base_intact(self):
        p = points.provisional([], self.BASE_D, self.BASE_C)
        self.assertEqual([x["key"] for x in p["drivers"]], ["VER", "NOR", "LEC"])
        self.assertEqual(p["awarded"], 0)
        for x in p["drivers"]:
            self.assertEqual(x["race_points"], 0)
            self.assertEqual(x["rank_change"], 0)

    def test_no_base_rows_means_no_projection(self):
        p = points.provisional([{"pos": 1, "code": "VER"}], [], [])
        self.assertEqual(p["drivers"], [])
        self.assertEqual(p["unmatched"], ["VER"])
        self.assertIsNone(p["constructors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
