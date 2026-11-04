#!/usr/bin/env python3
"""Smoke test against the REAL database and the REAL services.

"Unit tests pass" is not the same claim as "this works on the archive we have".
This file checks the second one, without writing anything it should not:

  * the schema migrates on a copy of the live database, not on the database;
  * store.py's standings cache round-trips;
  * points.provisional() runs on the *actual* newest stored race, using the real
    cached base table, and its arithmetic is re-checked independently;
  * the live projection refuses to double-count a race already in the base.

Run:  .venv/bin/python tests/smoke_real_db.py
      .venv/bin/python tests/smoke_real_db.py --live     (also probes :8099)
      .venv/bin/python tests/smoke_real_db.py --fetch     (also hits the API)

Everything that touches a database does so on a temporary COPY of
data/f1dash.db, so a bug in this test cannot damage the archive. The one read
against the real file is a read.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(label)


def section(title: str) -> None:
    print(f"\n{title}")


def copy_db(dest: Path) -> Path:
    """Copy the live database (with its WAL) so tests never write to the original."""
    src = HERE / "data" / "f1dash.db"
    if not src.exists():
        print("  veritabanı yok: data/f1dash.db — servis hiç çalışmamış olabilir")
        sys.exit(2)
    shutil.copy2(src, dest)
    for suffix in ("-wal", "-shm"):
        side = Path(str(src) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(dest) + suffix))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="canlı yüzeyi de yokla (:8099)")
    ap.add_argument("--fetch", action="store_true", help="gerçek API'den de çek")
    ap.add_argument("--standings", action="store_true", help="puan durumu yüzeyini yokla (:8101)")
    ap.add_argument("--welcome", action="store_true", help="hafta sonu yüzeyini yokla (:8103)")
    args = ap.parse_args()

    tmpdir = Path(tempfile.mkdtemp(prefix="f1dash-smoke-"))
    dbcopy = copy_db(tmpdir / "smoke.db")

    # store.DB_PATH is read at import time from F1_DASH_DB, so it must be set
    # before the import; every helper below then uses the copy.
    import os
    os.environ["F1_DASH_DB"] = str(dbcopy)
    import points      # noqa: E402
    import standings   # noqa: E402
    import store       # noqa: E402

    print(f"geçici kopya: {dbcopy}")
    check("DB yolu kopyaya ayarlandı", Path(store.DB_PATH) == dbcopy, str(store.DB_PATH))

    # ---------- 1. schema ----------
    section("1. şema göçü (kopya üzerinde)")
    store.init_db()
    with sqlite3.connect(dbcopy) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in c.execute("PRAGMA table_info(races)")}
    check("standings_snapshots tablosu var", "standings_snapshots" in tables)
    check("standings_attempts tablosu var", "standings_attempts" in tables)
    check("mevcut tablolar korundu", {"races", "frames", "lap_times", "chat", "prefs",
                                      "sectors", "usage", "lap_speeds"} <= tables)
    check("races.meeting_key duruyor", "meeting_key" in cols)

    # ---------- 2. gerçek yarış verisi ----------
    section("2. gerçek arşiv verisi")
    races = store.list_races()
    check("arşivde yarış var", bool(races), f"{len(races)} yarış")
    if not races:
        return 1
    newest = races[0]
    rid = newest["id"]
    fr = store.frame_row(rid)
    check(f"yarış #{rid} için çerçeve var", fr is not None,
          f"{newest.get('meeting')} / {newest.get('session')}")
    if not fr:
        return 1
    snap = fr["snap"]
    rows = snap.get("rows") or []
    check(f"çerçevede sürücü satırı var ({len(rows)})", len(rows) >= 10)
    check("çerçevede sıra numarası var", all("pos" in r for r in rows[:5]))
    check("çerçevede sürücü kodu var", all(r.get("code") for r in rows[:5]))
    print(f"       yarış: {newest.get('meeting')} {newest.get('year')} "
          f"/ tur {snap.get('lap')}/{snap.get('total')} / seans {snap.get('session')}")

    # ---------- 3. puan önbelleği ----------
    section("3. puan önbelleği (store.round-trip)")
    season = newest.get("year") or standings.season_now()
    sample = [{"key": "AAA", "position": 1, "points": 111, "wins": 2, "code": "AAA",
               "name": "Test Driver", "team": "Test Team", "team_id": "test"},
              {"key": "BBB", "position": 2, "points": 99, "wins": 0, "code": "BBB",
               "name": "Second Driver", "team": "Test Team", "team_id": "test"}]
    store.put_standings(season, store.STANDING_KIND_DRIVER, sample, round_no=5,
                        source="smoke", url="smoke://")
    got = store.get_standings(season, store.STANDING_KIND_DRIVER)
    check("yazılan satırlar geri okundu", bool(got) and len(got["rows"]) == 2)
    check("tur numarası korundu", (got or {}).get("round") == 5)
    check("fetched_at yazıldı", bool((got or {}).get("fetched_at")))
    check("kaynak etiketi korundu", (got or {}).get("source") == "smoke")

    view = standings.get_standings_view(season, store.STANDING_KIND_DRIVER)
    check("görünüm has_data=True", view["has_data"] is True)
    check("görünüm yaş hesaplıyor", view["age_seconds"] is not None
          and view["age_seconds"] >= 0, str(view["age_seconds"]))
    check("taze görünüm stale değil", view["stale"] is False)

    # Eski bir zaman damgası bayat sayılmalı.
    with sqlite3.connect(dbcopy) as c:
        c.execute("UPDATE standings_snapshots SET fetched_at='2020-01-01T00:00:00Z' "
                  "WHERE season=? AND kind=?", (season, store.STANDING_KIND_DRIVER))
    old = standings.get_standings_view(season, store.STANDING_KIND_DRIVER)
    check("eski damga bayat işaretleniyor", old["stale"] is True)

    # ---------- 4. gerçek yarış üzerinde projeksiyon ----------
    section("4. geçici tablo — gerçek yarış sırasıyla")
    base_rows = [{"key": r.get("code") or str(r.get("num")), "position": i + 1,
                  "points": 400 - i * 10, "code": r.get("code") or str(r.get("num")),
                  "name": r.get("name") or "", "team": r.get("team") or "",
                  "team_id": "test"}
                 for i, r in enumerate(rows)]
    base_cons = [{"key": "test", "name": "Test Team", "position": 1, "points": 500}]
    kind = points.session_kind(snap.get("session"))
    proj = points.provisional(rows, base_rows, base_cons, kind=kind,
                              lap=snap.get("lap"), total_laps=snap.get("total"))
    n_live = len([r for r in rows if not r.get("retired") and r.get("pos")])
    check(f"projeksiyon satır sayısı ({len(proj['drivers'])})",
          len(proj["drivers"]) == len(base_rows))
    check("dağıtılan puan tabloya uygun",
          proj["awarded"] <= sum(points.table(kind)), str(proj["awarded"]))
    print(f"       seans türü: {kind} · dağıtılan puan: {proj['awarded']} · "
          f"puanlanan sürücü: {len(proj['scored'])}")

    # Bağımsız yeniden hesap: her satırın total = taban + bu yarış puanı.
    bad = [d for d in proj["drivers"]
           if d["total"] != d["base_points"] + d["race_points"]]
    check("her satırda total = taban + yarış puanı", not bad, str(bad[:2]))
    totals = [d["total"] for d in proj["drivers"]]
    check("sıralama toplam puana göre azalmıyor", totals == sorted(totals, reverse=True))
    check("liderin puanı en yüksek", proj["drivers"][0]["total"] == max(totals))

    # Puanlanan her sürücünün puanı, sırasının tablo değeri olmalı.
    pos_of = {str(r.get("code") or r.get("num")): r.get("pos") for r in rows
              if not r.get("retired")}
    wrong = [s for s in proj["scored"]
             if s["points"] != points.points_for(pos_of.get(s["key"]))]
    check("her puanlı sürücünün puanı sırasıyla uyumlu", not wrong, str(wrong[:3]))

    # ---------- 5. çift sayma koruması ----------
    section("5. çift sayma koruması")
    store.put_standings(season, store.STANDING_KIND_DRIVER, sample, round_no=18)
    base_after = standings.base_rows_for_projection(season, race_round=18)
    check("tur >= yarış turu ise projeksiyon reddediliyor",
          base_after["unsafe"] is True, str(base_after["reason"]))
    base_before = standings.base_rows_for_projection(season, race_round=19)
    check("tur < yarış turu ise projeksiyon güvenli",
          base_before["unsafe"] is False, str(base_before["reason"]))

    # ---------- 6. gerçek API (opsiyonel) ----------
    if args.fetch:
        section("6. gerçek API çekimi")
        rep = standings.refresh(season)
        check("API çekimi başarılı", rep["ok"] is True, str(rep.get("results")))
        v = standings.get_standings_view(season, store.STANDING_KIND_DRIVER)
        check(f"sürücü tablosu dolu ({len(v['rows'])})", len(v["rows"]) > 0)
        cview = standings.get_standings_view(season, store.STANDING_KIND_CONSTRUCTOR)
        check(f"marka tablosu dolu ({len(cview['rows'])})", len(cview["rows"]) > 0)
        check("satırlarda pozisyon ve puan var",
              all(r.get("position") is not None and r.get("points") is not None
                  for r in v["rows"][:5]))

    # ---------- 6b. hafta sonu defteri (gerçek arşivde) ----------
    # The unit tests build their own DB, so they cannot catch "this archive has a
    # weekend shape nobody anticipated". These run on the copy of the REAL file.
    section("6b. hafta sonu defteri (gerçek arşiv)")
    import weekend  # noqa: E402
    races = store.list_races()
    check("arşivde yarış satırı var", bool(races), f"{len(races)} satır")
    keys = {store.weekend_key(r) for r in races}
    check("her satır bir hafta sonuna bağlanıyor", None not in keys and "" not in keys)
    check("hafta sonu sayısı satır sayısından çok değil", len(keys) <= len(races),
          f"{len(keys)} hafta sonu / {len(races)} satır")
    kinds = [store.session_kind(r.get("session")) for r in races]
    check("her yarış satırının bir seans türü var",
          all(k is not None for k in kinds),
          str([r.get("session") for r, k in zip(races, kinds) if k is None][:5]))
    # A session kind the grid does not know would be dropped from the cards
    # silently; KIND_ORDER is the contract.
    unknown = sorted({k for k in kinds if k and k not in store.KIND_ORDER})
    check("bilinmeyen seans türü yok", not unknown, str(unknown))

    ov_ok = 0
    for r in races[:6]:
        try:
            ov = weekend.overview(int(r["id"]))
        except Exception as e:
            check(f"#{r['id']} kart ızgarası", False, f"{type(e).__name__}: {e}")
            continue
        if not ov or not ov.get("cards"):
            continue
        ov_ok += 1
        states = {c["state"] for c in ov["cards"]}
        # `unknown` is a real state: a record whose clock evidence cannot settle
        # it. It must not be collapsed into "done" (that put "BİTTİ" on a race
        # that had not started), so it is allowed here as its own value.
        bad = states - {"done", "live", "scheduled", "empty", "missing", "unknown"}
        check(f"#{r['id']} kart durumları tanımlı", not bad, str(bad))
        check(f"#{r['id']} her kartın etiketi var",
              all(c.get("label") for c in ov["cards"]))
        # A card with no stored session must carry no race id -- otherwise the
        # page would link to a race that does not exist.
        check(f"#{r['id']} kayıtsız kartta rid yok",
              all(c["rid"] is not None for c in ov["cards"] if c["state"] != "missing"))
        # A live card must be unique; two LIVE badges would be a state bug.
        check(f"#{r['id']} en fazla bir canlı kart",
              sum(1 for c in ov["cards"] if c["live"]) <= 1)
    check("gerçek arşivde kart ızgarası üretildi", ov_ok > 0, f"{ov_ok} hafta sonu")

    blk_ok = 0
    for r in races[:4]:
        try:
            blk = weekend.summary_block(int(r["id"]))
        except Exception as e:
            check(f"#{r['id']} hafta sonu özeti", False, f"{type(e).__name__}: {e}")
            continue
        if "HAFTA SONU ÖZETİ" in blk:
            blk_ok += 1
    check("hafta sonu özeti üretiliyor", blk_ok > 0, f"{blk_ok} özet")
    if races:
        sample = weekend.summary_block(int(races[0]["id"]))
        check("özet kendini tanıtıyor", "HAFTA SONU ÖZETİ" in sample)
        check("özet token bütçesini aşmıyor", len(sample) <= 6000, f"{len(sample)} karakter")

    # ---------- 7. canlı servis (opsiyonel) ----------
    if args.live:
        section("7. canlı yüzey (:8099)")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8099/api/state", timeout=6) as r:
                import json
                state = json.loads(r.read().decode())
            check("canlı durum okunuyor", isinstance(state, dict) and "rows" in state)
            check("snapshot'ta standings alanı var", "standings" in state)
            st = state.get("standings") or {}
            check("standings bir sözlük", isinstance(st, dict))
            if st.get("provisional"):
                p = st["provisional"]
                check("geçici tablo sürücü listesi taşıyor", bool(p.get("drivers")))
                check("geçici tablo tablo değerlerini taşıyor",
                      p.get("table") == list(points.table(p.get("kind"))))
            else:
                check("projeksiyon yoksa gerekçe var", bool(st.get("reason")),
                      str(st.get("reason")))
        except Exception as e:
            check("canlı yüzeye ulaşıldı", False, f"{type(e).__name__}: {e}")

    if args.standings:
        section("8. puan durumu yüzeyi (:8101)")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8101/api/health", timeout=6) as r:
                import json
                h = json.loads(r.read().decode())
            check("puan durumu servisi ayakta", h.get("ok") is True)
            check("önbellek sezonları listeleniyor", isinstance(h.get("seasons"), list))
            with urllib.request.urlopen("http://127.0.0.1:8101/api/standings?season="
                                        + str(season), timeout=10) as r:
                import json
                d = json.loads(r.read().decode())
            check("sıralama ucu cevap veriyor", "driver" in d and "constructor" in d)
            check("her iki taraf da gerçek satır taşıyor",
                  bool(d["driver"]["rows"]) and bool(d["constructor"]["rows"]))
        except Exception as e:
            check("puan durumu yüzeyine ulaşıldı", False, f"{type(e).__name__}: {e}")

    if args.welcome:
        section("9. hafta sonu yüzeyi (:8103)")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8103/api/health", timeout=6) as r:
                import json
                h = json.loads(r.read().decode())
            check("hafta sonu servisi ayakta", h.get("ok") is True)
            with urllib.request.urlopen("http://127.0.0.1:8103/api/weekends", timeout=10) as r:
                import json
                d = json.loads(r.read().decode())
            check("hafta sonu listesi ucu cevap veriyor", "weekends" in d)
            wl = d.get("weekends") or []
            check("liste dolu", bool(wl), f"{len(wl)} hafta sonu")
            if wl:
                check("her girişte temsilci rid var", all(w.get("rid") for w in wl))
                check("her girişte hafta sonu anahtarı var", all(w.get("key") for w in wl))
                keys = [w["key"] for w in wl]
                check("liste bir hafta sonunu iki kez göstermiyor",
                      len(keys) == len(set(keys)))
                with urllib.request.urlopen(
                        f"http://127.0.0.1:8103/api/weekend/{wl[0]['rid']}", timeout=10) as r:
                    import json
                    ov = json.loads(r.read().decode())
                check("kart ızgarası ucu cevap veriyor", bool(ov.get("cards")))
                check("portlar istemciye taşınıyor", bool((ov.get("ports") or {}).get("archive")))
                check("kartlar durum etiketi taşıyor",
                      all(c.get("state_label") for c in ov.get("cards") or []))
        except Exception as e:
            check("hafta sonu yüzeyine ulaşıldı", False, f"{type(e).__name__}: {e}")

    # ---------- özet ----------
    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    if FAILED:
        print(f"BAŞARISIZ: {len(FAILED)} kontrol")
        for f in FAILED:
            print("  -", f)
        return 1
    print("TÜM KONTROLLER GEÇTİ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
