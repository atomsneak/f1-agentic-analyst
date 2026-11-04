#!/usr/bin/env python3
"""Analysis layer shared by both surfaces (live dashboard and archive browser).

Why this is its own module: the live app and the archive app answer different
questions ("what is happening now" vs "what happened, and why"), so they keep
separate UIs -- but they must agree on what the data means. MED8/TREND, the LLM
briefing, provenance handling, learned preferences and the stored row shape live
here exactly once.

Nothing in this module knows about the live feed state or about FastAPI.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import statistics as stats
import time
import urllib.request
from pathlib import Path

import store
import points


ARCHIVE_ROW_FIELDS = ("pos", "num", "code", "grid", "name", "team", "gap", "int", "laps",
                      "last", "best", "pit", "inpit", "retired", "tyre", "tyre_age", "stints", "color")

MED_WINDOW = 8          # "son 8 tur" -- a display window, not a data limit


def compact(s: dict) -> dict:
    """Slim copy for storage: sectors/segments are dropped (90% of the payload)."""
    return {
        "ok": True, "err": None,
        "updated": s.get("updated"), "server_time": s.get("server_time"),
        "meeting": s.get("meeting"), "official": s.get("official"), "session": s.get("session"),
        # session_type and countdown are stored with the frame on purpose: without
        # them a stored practice session is indistinguishable from a stored race,
        # and every later reader has to re-guess from the name -- which is how
        # "Practice 1" got analysed as a grand prix.
        "session_type": s.get("session_type"), "countdown": s.get("countdown"),
        "field_laps": s.get("field_laps"),
        "status": s.get("status"), "lap": s.get("lap"), "total": s.get("total"),
        "track": s.get("track"), "air": s.get("air"), "track_temp": s.get("track_temp"),
        "humidity": s.get("humidity"), "rain": s.get("rain"), "wind": s.get("wind"),
        "rows": [{k: r.get(k) for k in ARCHIVE_ROW_FIELDS} for r in (s.get("rows") or [])],
        "rc": s.get("rc") or [],
    }


# ---------- learned user preferences ----------
# The briefing used to have exactly one output shape, so a preference ("süreyi
# dakika:saniye olarak ver") had to be repeated on every question. Preferences are
# data now: they live in the `prefs` table (store.py), they are learned from what
# the user actually types, and they are injected into every briefing as a TERCİH
# line. Learning is deliberately dumb and deterministic -- a regex table, no model
# call -- so the assistant can never invent a preference for itself, and a model
# upgrade can never silently forget one.
PREF_DEFAULTS = {
    "time_format": "sec",      # sec -> 98.993 | mmss -> 1:38.993
    "answer_style": "prose",   # prose | list | table
    "language": "tr",          # tr | en
    "reasoning": "",           # off|minimal|low|medium|high; "" = surface decides
}

PREF_LABELS = {
    ("time_format", "mmss"): "süre = dakika:saniye (1:38.993)",
    ("time_format", "sec"): "süre = saniye (98.993)",
    ("answer_style", "list"): "biçim = liste halinde",
    ("answer_style", "table"): "biçim = tablo",
    ("answer_style", "prose"): "biçim = düz cümle",
    ("language", "tr"): "dil = Türkçe",
    ("language", "en"): "dil = English",
    ("reasoning", "off"): "düşünme = kapalı",
    ("reasoning", "minimal"): "düşünme = minimal",
    ("reasoning", "low"): "düşünme = düşük",
    ("reasoning", "medium"): "düşünme = orta",
    ("reasoning", "high"): "düşünme = yüksek",
}

# "liste halinde dakika saniye olarak" is an instruction; "son 8 tur olayı nedir"
# is a question. Order matters inside one key: the first matching rule wins, so
# "dakika saniye olarak" (mmss) is not re-read as "saniye olarak" (sec).
_LEARN_RULES = [
    ("time_format", "mmss", re.compile(r"(dakika\s*[:\-/]?\s*saniye|dk\s*[:\/]\s*sn|"
                                       r"mm\s*:\s*ss|m\s*:\s*ss|dakika:saniye)", re.I)),
    ("time_format", "sec", re.compile(r"(saniye\s+(?:olarak|cinsinden)|sadece\s+saniye|"
                                      r"sn\s+olarak|saniye\s+(?:ver|yaz)|salise\s+olarak)", re.I)),
    ("answer_style", "list", re.compile(r"(liste\s+halinde|liste\s+olarak|madde\s+madde|"
                                        r"satır\s+satır|alt\s+alta|sıralı\s+ver)", re.I)),
    ("answer_style", "table", re.compile(r"\btablo\b", re.I)),
    ("answer_style", "prose", re.compile(r"(düz\s+cümle|paragraf|sohbet\s+gibi)", re.I)),
    ("language", "en", re.compile(r"\b(ingilizce|english)\b", re.I)),
    ("language", "tr", re.compile(r"\b(türkçe|turkish)\b", re.I)),
    ("reasoning", "off", re.compile(r"düşünme\S*\s*(?:kapalı|off|kapat)", re.I)),
]
_LEARN_CUE = re.compile(r"\b(istiyorum|isterim|olsun|tercih\w*|lütfen|bundan sonra|"
                        r"her zaman|artık|hep|kaydet|öğren)", re.I)
_QUESTION = re.compile(r"(\?|\b(nedir|ne|neden|niye|nasıl|kim|hangi|kaç|mi|mı|midir|mıdır)\b)", re.I)


def prefs() -> dict[str, str]:
    """Effective preferences: stored values over defaults, unknown keys ignored."""
    out = dict(PREF_DEFAULTS)
    try:
        out.update({k: v for k, v in store.prefs_all().items() if k in PREF_DEFAULTS})
    except Exception:
        pass
    return out


def prefs_summary(p: dict | None = None) -> str:
    p = p or prefs()
    bits = [PREF_LABELS[(k, v)] for k, v in p.items() if (k, v) in PREF_LABELS]
    return " · ".join(bits)


def learn_prefs(text: str) -> dict[str, str]:
    """Persist any output preference stated in `text`; returns the changed keys.

    Detection works per clause, not per message: in "tek pit stratejileri nasıl
    çalıştı, liste halinde dakika saniye olarak" the first clause is a data
    question and teaches nothing, while the second is a format instruction. A
    clause is only trusted when it carries no question marker, or when it carries
    an explicit cue ("istiyorum", "bundan sonra", ...).
    """
    t = (text or "").strip()
    if not t or len(t) > 400:
        return {}
    clauses = [c for c in re.split(r"[,;.\n]|(?:\s+ve\s+)", t) if c.strip()]
    usable = [c for c in clauses if _LEARN_CUE.search(c) or not _QUESTION.search(c)]
    if not usable:
        return {}
    cur, changed, seen = prefs(), {}, set()
    for clause in usable:
        for key, value, rx in _LEARN_RULES:
            # One value per key: the first matching rule wins, so "dakika saniye
            # olarak" is not immediately re-read as "saniye olarak" by the next one.
            if key in seen or not rx.search(clause):
                continue
            seen.add(key)
            if cur.get(key) == value:
                continue
            try:
                store.pref_set(key, value)
            except Exception as e:      # never lose the answer over a preference write
                print("prefs: kayıt başarısız:", e)
                continue
            changed[key] = value
    return changed


def fmt_time(sec, p: dict | None = None) -> str:
    """Format a lap time/gap in the user's preferred unit. Never invents a value."""
    p = p or prefs()
    if sec is None or sec == "":
        return "-"
    try:
        v = float(sec)
    except (TypeError, ValueError):
        return str(sec)
    if p.get("time_format") == "mmss":
        m, s = divmod(abs(v), 60)
        sign = "-" if v < 0 else ""
        return f"{sign}{int(m)}:{s:06.3f}"
    return f"{v:.3f}"


def fmt_delta(sec) -> str:
    """Trend/gap deltas stay in seconds (+/-) whatever the lap-time preference is:
    '-0.478 sn/tur' is meaningful, '-0:00.478' is not."""
    if sec is None:
        return "-"
    try:
        v = float(sec)
    except (TypeError, ValueError):
        return str(sec)
    return f"{v:+.3f}"


# ---------- full lap data ----------
def _ranges(nums) -> str:
    """[3,4,5,9] -> '3-5,9' so a long gap list stays short."""
    nums = sorted(nums)
    if not nums:
        return ""
    out, a, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{a}-{prev}" if prev > a else f"{a}")
        a = prev = n
    out.append(f"{a}-{prev}" if prev > a else f"{a}")
    return ",".join(out)


def _stint_spans(stints, last_lap: int) -> list[tuple[str, int, int]]:
    """[(compound, first_lap, last_lap)] from the per-stint lap counts in a row."""
    spans, lap = [], 1
    for st in stints or []:
        n = st.get("laps")
        if not isinstance(n, int) or n <= 0:
            continue
        comp = (st.get("compound") or "?").upper()
        spans.append((comp, lap, min(lap + n - 1, last_lap)))
        lap += n
        if lap > last_lap:
            break
    if lap <= last_lap:
        spans.append(("?", lap, last_lap))
    return spans


def lap_lines(rid: int, p: dict | None = None, times: dict | None = None,
              tail: int | None = None) -> list[str]:
    """Every stored lap time, per driver, grouped by stint.

    This is the answer to "do you have all the laps": yes -- lap_times holds them.
    The briefing used to expose only LAST/BEST/MED8/TREND, which made a complete
    archive look like a summary. `tail` keeps the last N laps only (context budget).
    """
    p = p or prefs()
    times = store.all_lap_times(rid) if times is None else times   # num -> {lap: sec}
    if not times:
        return []
    meta = store.race_driver_meta(rid)
    lines = []
    for num in sorted(times, key=lambda n: (meta.get(n, {}).get("grid") or 99, n)):
        hist = dict(times[num])
        if not hist:
            continue
        last = max(hist)
        if tail:
            hist = {l: v for l, v in hist.items() if l > last - tail}
        code = meta.get(num, {}).get("code") or num
        spans = _stint_spans(meta.get(num, {}).get("stints"), last)
        chunks, covered = [], set()
        for comp, a, b in spans:
            vals = [(l, hist[l]) for l in range(a, b + 1) if l in hist]
            if not vals:
                continue
            covered.update(l for l, _ in vals)
            chunks.append(f"[{comp} {a}-{b}] " + " ".join(fmt_time(v, p) for _, v in vals))
        extra = [l for l in sorted(hist) if l not in covered]
        if extra:
            chunks.append("[?] " + " ".join(f"L{l}={fmt_time(hist[l], p)}" for l in extra))
        gaps = [l for l in range(min(hist), last + 1) if l not in hist]
        note = f" (kayıt yok: tur {_ranges(gaps)})" if gaps else ""
        lines.append(f"{code} | " + " | ".join(chunks) + note)
    return lines


_POS_TTL = 5.0
_pos_memo: dict[int, tuple[float, dict]] = {}


def _pos_hist(rid: int) -> dict[str, list[tuple[int, int]]]:
    """Lap -> order of every car, read from the stored frames.

    Position per lap IS recorded: every frame keeps the full POS column, so
    "where was LEC at the end of stint 1" has an exact answer in this database.
    It simply never reached the briefing, and an agent that cannot see it says
    "bu veri feed'de yok" -- which was a wrong answer, not a missing datum.

    Cached for a few seconds because building it parses every frame of the race
    (~110 ms for 56 laps); positions only change when a frame lands.
    """
    hit = _pos_memo.get(rid)
    now = time.time()
    if hit and now - hit[0] < _POS_TTL:
        return hit[1]
    hist: dict[str, list[tuple[int, int]]] = {}
    try:
        for lap in store.race_lap_numbers(rid):
            fr = store.frame_row(rid, lap)
            for r in (fr.get("snap") or {}).get("rows") or []:
                code, pos = r.get("code"), r.get("pos")
                if code and pos is not None:
                    hist.setdefault(code, []).append((lap, int(pos)))
    except Exception:
        return hit[1] if hit else {}
    _pos_memo[rid] = (now, hist)
    return hist


def pos_block(snap: dict, max_chars: int = 4000) -> list[str]:
    """Position history, written as change points instead of one number per lap.

    Each entry is a lap RANGE, not a change point: "25-39:2" means P2 from lap 25
    through 39. Ranges cost almost nothing over bare change points (2116 vs 1852
    characters here) and remove all inference -- "where was he on lap 30" is a
    direct lookup instead of "find the last change at or before 30", which the
    model visibly fumbled in testing while still landing on the right answer.
    """
    rid = snap.get("race_id")
    if not rid:
        return []
    hist = _pos_hist(int(rid))
    if not hist:
        return []
    lines = []
    for code in sorted(hist, key=lambda c: (hist[c][-1][1] if hist[c] else 99, c)):
        lines.append(f"{code} | " + _ranges_of(hist[code]))
    head = ("TUR BAZLI SIRALAMA (biçim tur aralığı:sıra — '25-39:2' = tur 25'ten 39'a kadar "
            "P2. Aralık dışına düşen tur yoktur, her tur bir aralığa girer) — bu blok varken "
            "'tur bazlı pozisyon kaydı yok' denmez. 'Stint 1'i kaçıncı bitirdi' = stintin son "
            "turunun aralık değeri; 'ne zaman 3.'lüğe düştü' = o aralığın başlangıç turu:")
    body = lines
    if sum(len(x) for x in lines) > max_chars:
        body = [f"{c} | " + _ranges_of(hist[c][-12:]) for c in hist]
        head += " bağlam bütçesi nedeniyle sürücü başına son 12 tur."
    return [head] + body


def _race_meta(rid) -> str:
    """Tarih + resmî ad — 'bu yarış ne zamandı' sorusunun cevabı races tablosunda."""
    if not rid:
        return ""
    try:
        r = store.get_race(int(rid)) or {}
    except Exception:
        return ""
    start = str(r.get("start_date") or "")
    end = str(r.get("end_date") or "")
    bits = []
    if start:
        # Full timestamp, not just the day: "saat kaçta başladı" is answerable too.
        bits.append(f"başlangıç={start.replace('T', ' ')}")
    if end:
        bits.append(f"bitiş={end.replace('T', ' ')}")
    if r.get("year"):
        bits.append(f"sezon={r['year']}")
    if r.get("official"):
        bits.append(f"resmî_ad={r['official']}")
    if r.get("final_status"):
        bits.append(f"durum={r['final_status']}")
    return ("SEANS/YARIŞ KİMLİĞİ (" + " | ".join(bits) + ") — takvim tarihi burada yazıyor, "
            "'veride tarih yok' denmez.") if bits else ""


def _driver_facts(rid: int) -> dict[str, dict]:
    """Per driver: full name, retirement, pit-lane laps -- straight from the frames.

    Audited against the stored schema: these three were recorded and never reached
    the briefing, so the agent answered "bu veri feed'de yok" to a question the
    database could answer. "COL yarış dışı kalmadı" was the visible symptom: the
    RETIRED flag existed, the model could only infer from a lap count and hedged
    into a contradiction.
    """
    hit = _pos_memo.get(("facts", rid))
    now = time.time()
    if hit and now - hit[0] < _POS_TTL:
        return hit[1]
    facts: dict[str, dict] = {}
    try:
        for lap in store.race_lap_numbers(rid):
            fr = store.frame_row(rid, lap)
            for r in (fr.get("snap") or {}).get("rows") or []:
                code = r.get("code")
                if not code:
                    continue
                f = facts.setdefault(code, {"name": "", "retired": None, "laps": None,
                                            "pits": []})
                if r.get("name"):
                    f["name"] = r["name"]
                f["laps"] = r.get("laps")
                if r.get("inpit"):
                    f["pits"].append(int(lap))
                if r.get("retired"):
                    f["retired"] = int(lap)
    except Exception:
        return hit[1] if hit else {}
    _pos_memo[("facts", rid)] = (now, facts)
    return facts


def drivers_block(snap: dict) -> list[str]:
    """Names, retirements and pit-stop laps -- the fields the audit found missing."""
    rid = snap.get("race_id")
    if not rid:
        return []
    facts = _driver_facts(int(rid))
    if not facts:
        return []
    order = {c: i for i, c in enumerate(sorted(facts))}
    names = ", ".join(f"{c}={f['name']}" for c, f in
                      sorted(facts.items(), key=lambda kv: order[kv[0]]) if f["name"])
    out = []
    if names:
        out.append("SÜRÜCÜLER (kod=tam ad): " + names)
    ret = [(c, f) for c, f in facts.items() if f["retired"]]
    if ret:
        out.append("YARIŞ DIŞI (RETIRED bayrağı — 'neden çekildi' sorusunun sebebi bu "
                   "bayrakta YOK, yalnızca çekildiğini gösterir): " +
                   ", ".join(f"{c} (tur {f['retired']}, {f['laps']} tur tamamladı)"
                             for c, f in sorted(ret)))
    pits = [(c, f["pits"]) for c, f in facts.items() if f["pits"]]
    if pits:
        out.append("PİT LANE TURLARI (araca girilen tur — pit sayısıyla tutarlı olmalı): " +
                   " | ".join(f"{c} {','.join(str(l) for l in ls)}"
                              for c, ls in sorted(pits)))
    return out


_SEG_NAME = {2048: "P", 2049: "S", 2051: ".", None: "?"}


def _seg_str(raw) -> str:
    """',2048,2051,2049' -> 'P.S' (P=kişisel en iyi, S=seans en iyisi, .=gelişme yok).

    The mini sectors carry a STATUS CODE, never a duration -- so they are printed
    as a flag sequence and never summed or compared as times. That is the whole
    honest use of them: "who was purple where", not "how many milliseconds".
    """
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            # An empty field is an absent mini sector (OpenF1 sends null there).
            # It must still take a slot: dropping it would shift every following
            # position and silently misalign two drivers' grids against each other.
            out.append("?")
            continue
        try:
            out.append(_SEG_NAME.get(int(part), "?"))
        except ValueError:
            out.append("?")
    return "".join(out)


def best_lap_block(snap: dict, max_chars: int = 3000) -> list[str]:
    """Fastest lap of every driver: speed at each boundary + mini-sector flags.

    This is the comparison material for "LEC vs HAM, where did the time go".
    Sector durations answer it in seconds; these speeds answer it in km/h, and
    the mini-sector flags say who was purple where. All three are stored, none
    of them were in the briefing.
    """
    rid = snap.get("race_id")
    if not rid:
        return []
    times = store.all_lap_times(int(rid))
    if not times:
        return []
    meta = store.race_driver_meta(int(rid))
    lines = []
    for num, laps in times.items():
        if not laps:
            continue
        best_lap = min(laps, key=lambda l: laps[l])
        best_sec = laps[best_lap]
        secs = store.speeds_for(int(rid), best_lap).get(str(num)) or {}
        row = store.sectors_for(int(rid), best_lap)[0].get(str(num)) or {}
        code = (meta.get(str(num)) or {}).get("code") or num
        bits = [f"tur {best_lap}", f"{best_sec:.3f}sn"]
        # The sector times MUST be here, not only in the POS table: that table
        # carries the CURRENT lap's sectors, so a model asked about the best lap
        # silently pairs the best-lap duration with another lap's splits (it did
        # exactly that in testing, quoting lap 56's 26.164 as lap 42's S1).
        # Each sector is rendered on its own: on a lap still in progress the feed
        # has published S1/S2 and not S3 yet, and formatting the whole triple on a
        # single "s1 is not None" guard crashed /api/chat with
        # "unsupported format string passed to NoneType" (real 500s, race 21).
        # A sector the feed never published is printed as "-", never interpolated.
        sec_parts = [row.get(k) for k in ("s1", "s2", "s3")]
        if any(v is not None for v in sec_parts):
            bits.append("S=" + "/".join(
                f"{v:.3f}" if isinstance(v, (int, float)) else "-" for v in sec_parts))
        if any(secs.get(k) is not None for k in ("i1", "i2", "st")):
            bits.append(f"hiz i1/i2/st={secs.get('i1') or '-'}/{secs.get('i2') or '-'}/{secs.get('st') or '-'}")
        if row.get("segs"):
            flags = "/".join(_seg_str(s) for s in row["segs"])
            if flags.replace("/", "").replace("?", ""):
                bits.append(f"mini={flags}")
        lines.append(f"{code} | " + " ".join(bits))
    if not lines:
        return []
    head = ("EN İYİ TUR: o turun KENDİ sektör süreleri (S=), hızları (km/h) ve mini sektör "
            "bayrakları — i1=S1 sonu hızı, i2=S2 sonu hızı, st=hız tuzağı. Bu satırdaki "
            "süreler o sürücünün en hızlı turuna aittir; POS tablosundaki sektörler GÜNCEL "
            "turun sektörleridir, karıştırma. mini bayrakları: P=kişisel en iyi, S=seans en iyisi, "
            ".=gelişme yok (bunlar SÜRE DEĞİL, durum kodudur — toplama/çıkarma yapılmaz; "
            "'nerede mordu' sorusunu cevaplar, 'kaç ms kaybetti' sorusunu DEĞİL):")
    body = lines
    if sum(len(x) for x in lines) > max_chars:
        body = lines[:max_chars // 40]
        head += " bağlam bütçesi nedeniyle ilk sürücüler."
    return [head] + body


def _ranges_of(seq: list[tuple[int, int]]) -> str:
    """[(1,3),(2,3),(3,4)] -> '1-2:3 3:4'."""
    out, i = [], 0
    while i < len(seq):
        lap, pos = seq[i]
        j = i
        while j + 1 < len(seq) and seq[j + 1][1] == pos and seq[j + 1][0] == seq[j][0] + 1:
            j += 1
        a, b = seq[i][0], seq[j][0]
        out.append(f"{a}:{pos}" if a == b else f"{a}-{b}:{pos}")
        i = j + 1
    return " ".join(out)


def lap_block(snap: dict, p: dict | None = None, max_chars: int = 42000) -> list[str]:
    """Full per-lap section of the briefing, with a context-budget fallback."""
    rid = snap.get("race_id")
    if not rid:
        return []
    p = p or prefs()
    times = store.all_lap_times(int(rid))
    lines = lap_lines(int(rid), p, times)
    if not lines:
        return []
    n_laps = sum(len(h) for h in times.values())
    head = (f"TÜM TURLAR (her tur burada: {len(lines)} sürücü, {n_laps} tur kaydı; "
            f"biçim={p.get('time_format')}) — bu blok varken 'tur tur veri yok / sadece özet var' "
            f"denmez; gerçekten eksik tur varsa satır sonunda 'kayıt yok: tur ...' yazar:")
    body = lines
    if sum(len(x) for x in lines) > max_chars:
        body = lap_lines(int(rid), p, times, tail=15)
        head += " bağlam bütçesi nedeniyle sürücü başına son 15 tur."
    return [head] + body


def _surface_text(s) -> str:
    """Who is asking, and what this request can actually see.

    The two surfaces share one database but not one request. Without this line
    the model cannot tell a live race from a recorded one, so "what happened in
    the last race" gets answered either from the wrong race or from thin air.
    It also states the limit plainly: this is a single request/response call with
    no tools, so when asked for data outside the snapshot the honest move is to
    say where it lives, not to guess.
    """
    port = ARCHIVE_URL.rsplit(":", 1)[-1].rstrip("/")
    if s.get("past_of_current_race"):
        return (f"ORTAM: CANLI SEANSIN GEÇMİŞ TURU — canlı yakalama sürüyor, ama aşağıdaki veri "
                f"tur {s.get('lap')} kaydından geliyor; 'şu an' değil. Anlık tablo için canlı "
                f"arayüz kullanılır.")
    if not s.get("archived"):
        return (f"ORTAM: CANLI YAKALAMA (port 8099). Bu seans şu an sürüyor; aşağıdaki tablo "
                f"tur {s.get('lap')}/{s.get('total')} anlık görüntüsüdür. Yalnız bu görüntüyü "
                f"görüyorsun. Bu seansın geçmiş turları ve bitmiş diğer seanslar aynı "
                f"veritabanında, arşiv arayüzünde ({ARCHIVE_URL}) duruyor. Araç çağıramazsın: "
                f"bu mesajda olmayan bir veriyi isteyen soruya 'bu görüntüde yok, arşivde var' "
                f"de; sayı uydurma.")
    where = f"tur {s.get('lap')} verisi" if s.get("lap") is not None else "son durum verisi"
    return (f"ORTAM: ARŞİV TARAYICISI (port {port}), kayıtlı bir seansın {where}. "
            f"Canlı yayın değil, geçmiş kayıt. Canlı yakalama ayrı arayüzde (port 8099) "
            f"ve yalnız o an süren seansı gösterir.")


def _session_line(s) -> str:
    """The identity line, written from the session CLASS rather than from a guess.

    This one line is where the "practice analysed as a race" bug was visible:
    it used to print `lap=56/56` with no indication that, in a practice session,
    56 is a session-clock tick and not a race distance. A reader (human or model)
    had no way to tell P1 from a finished 56-lap grand prix, so it described the
    practice as one.
    """
    stype = s.get("session_type") or points.session_type(s.get("session"))
    kind = stype.get("kind")
    bits = [f"SESSION: {s.get('meeting')} - {s.get('session')}",
            f"seans_tipi={stype.get('label')} ({kind})"]
    if s.get("countdown"):
        bits.append(f"KALAN SÜRE={s['countdown']}")
    bits.append(f"status={s.get('status')}")
    if stype.get("has_race_distance"):
        bits.append(f"tur={s.get('lap')}/{s.get('total')}")
    else:
        # No race distance exists here. Saying "lap 56/56" would be a false
        # statement about the session, so it is replaced by what is true.
        bits.append(f"seans_sayacı={s.get('lap')}"
                    " (YARIŞ TURU DEĞİL — bu seansta yarış mesafesi yok)")
        if s.get("field_laps") is not None:
            bits.append(f"sahada_en_çok_tur={s.get('field_laps')}")
    if stype.get("segments"):
        bits.append(f"segmentler={'/'.join(stype['segments'])}")
    bits.append(f"track={s.get('track')}")
    bits.append(_completion_line(s, stype))
    return " | ".join(bits)


def _phase_of(s) -> str:
    """Cheap phase lookup for the briefing header. Never raises."""
    rid = s.get("race_id")
    if rid is None:
        return ""
    try:
        import weekend
        row = store.get_race(int(rid))
        if row is None:
            return ""
        stype = s.get("session_type") or points.session_type(s.get("session"))
        max_lap = s.get("lap") if stype.get("has_race_distance") else s.get("field_laps")
        return weekend.race_phase(row, max_lap=max_lap)["phase"]
    except Exception:
        return ""


def _completion_line(s, stype: dict) -> str:
    """Does the EVENT have an end, and how complete is our record of it?

    Without this line the model answered from `status=Started` plus `tur=57/58`
    and told the user a three-week-old grand prix "has not finished yet, no
    chequered flag is visible" -- true about the file, false about the race.
    The mirror-image error happened in the archive card grid, where "has frames
    and is stale" was read as "finished" for a race that had not started.

    Three separate facts, stated separately, because collapsing them is what
    produced both bugs:

      * SEANS BİTTİ / SÜRÜYOR / BAŞLAMADI -- about the event
      * KAYIT TAM / KAYIT KESİK         -- about our file
      * what to do about it              -- an instruction the model can follow
    """
    rid = s.get("race_id")
    row = None
    if rid is not None:
        try:
            row = store.get_race(int(rid))
        except Exception:
            row = None
    if row is None:
        # A live snapshot with no stored row yet: fall back to what the frame
        # itself says, and be explicit that it is the frame we mean.
        status = str(s.get("status") or "")
        ended = "ends" in status.lower() or "finish" in status.lower()
        return ("SEANS DURUMU: " + ("bitti (feed)" if ended else "sürüyor (feed)")
                + " | KAYIT: bu anlık görüntü")

    try:
        import weekend
        # An archived frame knows its own lap; a live one carries the race lap.
        max_lap = s.get("lap") if stype.get("has_race_distance") else s.get("field_laps")
        info = weekend.race_phase(row, max_lap=max_lap)
    except Exception as e:
        return f"SEANS DURUMU: belirlenemedi ({type(e).__name__})"

    phase = info["phase"]
    if phase == "not_started":
        word = "BAŞLAMADI"
    elif phase == "running":
        word = "SÜRÜYOR"
    elif phase == "no_data":
        word = "VERİ YOK"
    elif phase == "unknown":
        # Its own word, not "BİTTİ". The default branch used to sweep `unknown`
        # into "BİTTİ", which is how the assistant ended up describing a race
        # that had not started as a finished one.
        word = "BİLİNMİYOR"
    else:
        word = "BİTTİ"

    if phase in ("finished",) and info["record_complete"]:
        rec = "KAYIT TAM"
    elif phase in ("finished",) and not info["record_complete"]:
        rec = (f"KAYIT KESİK (son kayıtlı tur {info['last_lap']}"
               f"/{info['total']})" if info["last_lap"] is not None and info["total"]
               else "KAYIT KESİK")
    elif phase == "no_data":
        rec = "KAYIT YOK"
    elif phase == "not_started":
        rec = "KAYIT YOK (henüz başlamadı)"
    elif phase == "unknown":
        rec = "BELİRSİZ"
    else:
        rec = "KAYIT SÜRÜYOR"
    if phase == "not_started":
        # A race row can exist before the race with a few stray frames on it
        # (measured: the Mexico City race row held three frames a day early).
        # Saying "no record" would be false; saying "record" invites the model
        # to analyse a race that has not run. Name it for what it is.
        rec = "ISKARTA KAYIT VAR — YARIŞ VERİSİ SAYMA"

    rules = []
    if phase == "finished" and info["record_complete"]:
        rules.append("Bu seans GEÇMİŞTE kaldı: geçmiş zaman kullan, 'henüz bitmedi' "
                     "veya 'damalı bayrak görünmüyor' deme.")
    elif phase == "finished" and not info["record_complete"]:
        rules.append("Bu seans GEÇMİŞTE kaldı ve bitti, ama elimizdeki kayıt son turda "
                     "kesiliyor: sonucu (kazanan/bitiş sırası) bu kayıttan VERME; "
                     "'kayıt son turda kesiliyor, bitiş sırası arşivde yok' de. "
                     "'Yarış henüz bitmedi' demek YANLIŞ -- yarış bitti, kayıt eksik.")
    elif phase == "not_started":
        rules.append("Bu seans HENÜZ BAŞLAMADI (başlangıç saati ileride). Geçmiş zaman "
                     "kullanma, sonuç veya kazanan üretme; varsa erken/ıskarta kayıtları "
                     "yarış verisi sayma.")
    elif phase == "running":
        rules.append("Bu seans sürüyor: sonuç değil, o anki durum anlatılır.")
    elif phase == "unknown":
        rules.append("Bu kaydın bitti mi sürüyor mu olduğu belirlenemiyor (başlangıç "
                     "saati kaydın zaman dilimiyle çelişiyor). 'Bitti' veya 'sürüyor' "
                     "DEME; sonuç üretme, belirsiz olduğunu söyle.")

    line = f"SEANS DURUMU: {word} | {rec} ({info['reason']})"
    if rules:
        line += " KURAL: " + " ".join(rules)
    return line


def context_text(s, p: dict | None = None):
    """Compact, number-dense briefing for the LLM."""
    p = p or prefs()
    if s.get("past_of_current_race"):
        head = "GEÇMİŞ TUR KAYDI (canlı seansın önceki bir turu — o anın kaydı)"
    elif s.get("archived"):
        # "Geçmiş seans" is a claim, and it was false for a race row created
        # before the race with stray frames. The head line is the first thing
        # the model reads, so it must carry the corrected verdict too.
        if _phase_of(s) == "not_started":
            head = ("ARŞİV KAYDI (bu seans HENÜZ BAŞLAMADI — eldeki çerçeveler ıskarta, "
                    "yarış verisi değil)")
        else:
            head = "ARŞİV KAYDI (geçmiş seans, canlı değil — veriler o anın kaydından geliyor)"
    else:
        head = "CANLI"
    out = [f"{head} | " + _session_line(s)]
    # The calendar date lives in the races table, not in the frame, so it never
    # reached the briefing: "bu yarış ne zamandı" answered "veride tarih yok" while
    # start_date/official sat one table away. Audited and fixed here.
    meta = _race_meta(s.get("race_id"))
    if meta:
        out.append(meta)
    out.append(_surface_text(s))
    out.append(f"TERCİH (kullanıcının kalıcı tercihi — cevabını buna göre yaz): {prefs_summary(p)}")
    # Extra deterministic blocks (an earlier lap, another race, the archive menu)
    # attached by the caller after routing. They are real stored data, so they go
    # after the identity line and before the numbers they are meant to explain.
    for blk in s.get("extra_blocks") or []:
        if blk:
            out.append(blk)
    missing = s.get("missing") or []
    if s.get("source") and s.get("source") != "live":
        out.append(f"KAYNAK: bu tur {s.get('source')} arşivinden yeniden kuruldu "
                   f"(canlı feed kaydı yok). Eksik alanlar: {', '.join(missing) or 'yok'}.")
    elif missing:
        out.append(f"KAYNAK: canlı kayıt, eksik alanlar: {', '.join(missing)}.")
    if missing:
        out.append("EKSİK ALAN KURALI: aşağıda eksik listelenen alanlar hakkında soru gelirse "
                   "tahmin etme; 'bu tur için o veri arşivde yok' de.")
    if s.get("air") is not None:
        out.append(f"WEATHER: air={s.get('air')}C track={s.get('track_temp')}C "
                   f"humidity={s.get('humidity')}% rain={s.get('rain')} wind={s.get('wind')}m/s")
    # Sector columns exist only when the sector table has this lap; a driver with no
    # sectors keeps '-' rather than a fake 0, so the model can tell "not published"
    # from "zero".
    has_sec = any(r.get("s1") is not None for r in (s.get("rows") or []))
    head = "POS | DRV | GRID | GAP | INT | LAPS | LAST | BEST | PIT | TYRE(age) | MED_last8 | TREND"
    if has_sec:
        head += " | S1 | S2 | S3 | IDEAL | LOSS"
    out.append(head + " | TEAM")
    for r in s.get("rows") or []:
        tyre = f"{r['tyre']}({r['tyre_age']})" if r.get("tyre") else "-"
        line = (f"{r['pos']} | {r['code']} | {r.get('grid') or '-'} | {r['gap']} | {r['int'] or '-'} | {r['laps']} | "
                f"{fmt_time(r['last'], p) if r['last'] else '-'} | "
                f"{fmt_time(r['best'], p) if r['best'] else '-'} | {r['pit']} | {tyre} | "
                f"{fmt_time(r['med8'], p) if r['med8'] is not None else '-'} | "
                f"{fmt_delta(r['trend']) if r['trend'] is not None else '-'}")
        if has_sec:
            def _s(v):
                return f"{v:.3f}" if isinstance(v, (int, float)) else "-"
            line += (f" | {_s(r.get('s1'))} | {_s(r.get('s2'))} | {_s(r.get('s3'))} | "
                     f"{_s(r.get('ideal'))} | {_s(r.get('loss'))}")
        out.append(line + f" | {r['team']}")
    laps = lap_block(s, p)
    if laps:
        out.extend(laps)
    pos = pos_block(s)
    if pos:
        out.extend(pos)
    drv = drivers_block(s)
    if drv:
        out.extend(drv)
    bl = best_lap_block(s)
    if bl:
        out.extend(bl)
    if s.get("rc"):
        out.append("RACE CONTROL (last 12):")
        for m in s["rc"][-12:]:
            out.append(f"  lap {m.get('lap')} {m.get('cat')}: {m.get('msg')}")
    # Penalties as a separate, computed block. Not left to the model reading the
    # race-control text: "5 SECOND TIME PENALTY" and "UNDER INVESTIGATION" sit in
    # the same stream and are not the same thing, and a served penalty repeats its
    # own text. penalties.py resolves it; the model only reads the result.
    pen = s.get("penalties")
    if pen is None:
        try:
            import penalties as _pen
            msgs = s.get("rc") or []
            if rid := s.get("race_id"):
                stored = stored_rc(rid)
                if len(stored) > len(msgs):
                    msgs = stored
            pen = _pen.parse(msgs) if msgs else None
        except Exception:
            pen = None
    if pen is not None:
        try:
            import penalties as _pen
            out.append(_pen.summary(pen))
        except Exception:
            pass
    # The session-kind rules are stated in the DATA, not only in the system
    # prompt. The prompt is a general instruction; this line is about THIS
    # snapshot, and it is the thing that stops a practice session being read as
    # a race even when the model ignored the prompt.
    out.append(_session_rules(s))
    out.append("NOTES: GRID = starting position (GridPos) — use it for 'where did X start' questions. "
               "If GRID is '-' the feed has no grid slot for that car (pit lane start or retired before grid). "
               f"MED_last8 = median of the last {MED_WINDOW} laps only, TREND = newest minus oldest lap in "
               "that same window (negative = getting faster). Both are SHORTCUTS, not the data: the TÜM TURLAR "
               "block lists every individual lap time per driver, grouped by stint, so 'show me all laps' / "
               "'which lap did he lose time' questions must be answered from there. "
               "YASAK: TÜM TURLAR bloğu varken 'tur tur detay yok', 'aşınma eğrisi yok', 'sadece özet var' "
               "deme — her turun süresi orada; eksik olan tek şey (varsa) 'kayıt yok: tur ...' notunda yazar. "
               "GapToLeader can be stale during pit cycles; trust POS for order. "
               "S1/S2/S3 = sektör süreleri (sn, o tur için); IDEAL = sürücünün kendi en iyi "
               "sektörlerinin toplamı, LOSS = gerçek tur eksi IDEAL (kaybedilen süre). Sektör "
               "'-' ise o sürücü için sektör yayınlanmamıştır; tahmin etme.")
    return "\n".join(out)


def _session_rules(s) -> str:
    """Per-snapshot rules that follow from the session kind.

    Written as data so the answer is anchored to this snapshot rather than to a
    general instruction in the system prompt. Each sentence here exists because
    its absence produced a specific wrong answer, quoted in the comment.
    """
    stype = s.get("session_type") or points.session_type(s.get("session"))
    kind = stype.get("kind")
    bits = [f"SEANS KURALI (seans_tipi={kind}):"]
    # "kaç tur attı" -> the model summed the session counter and answered 56 for
    # a driver whose LAPS column said 29.
    bits.append("Bir sürücünün kaç tur attığı SADECE POS tablosundaki LAPS kolonundan "
                "veya TÜM TURLAR bloğundaki o sürücünün kayıt sayısından okunur; "
                "seans sayacından (seans_sayacı=) türetilmez.")
    if not stype.get("has_race_distance"):
        bits.append("Bu seansta YARIŞ MESAFESİ YOK. 'tur=N/M' biçiminde bir yarış turu "
                    "yoktur; seans_sayacı yalnız saat tikidir. '56 turluk yarış', 'yarış "
                    "mesafesi', 'bitiş turu' gibi ifadeler bu seans için YANLIŞTIR.")
        bits.append(f"Tempo için doğru terim: '{stype.get('pace_label')}'. "
                    "'Yarış temposu' demek bu seansta yanlış bir iddiadır.")
    if stype.get("is_practice"):
        bits.append("Antrenman: sıralama/grid dağıtılmaz, puan verilmez. Sürücüler farklı "
                    "program ve lastikle tur atar; süreler doğrudan kıyaslanabilir değildir. "
                    "Uzun stint ortalamaları için yalnız temiz (pit/yavaş tur içermeyen) "
                    "turları kullan ve lastik bileşimi + yaşını yaz.")
    if stype.get("is_qualifying"):
        segs = stype.get("segments") or []
        bits.append("Sıralama: puan verilmez; grid bu seansta belirlenir. Segmentler: "
                    + "/".join(segs) + ". Her segment ayrı bir eleme dilimidir; bir sürücünün "
                    "hangi segmentte elendiği o segmentteki tur zamanıyla ilgilidir.")
        if s.get("countdown"):
            bits.append(f"Kalan süre {s['countdown']}. Süre bitince segment kapanır ve "
                        "kesilen sürücüler o segmentteki sıralarıyla kalır.")
        bits.append("Sıralamada 'yarış temposu' veya 'stint' analizi YAPILMAZ; tek tur "
                    "temposu (BEST ve temiz tur serileri) konuşulur.")
    if stype.get("is_sprint") or stype.get("is_qualifying"):
        bits.append("SPRINT AYRIMI: 'Sprint' bir YARIŞTIR (puan verir, ~100 km, pit "
                    "stratejisi vardır). 'Sprint Qualifying' / 'Sprint Shootout' ise bir "
                    "SIRALAMA seansıdır (puan vermez, grid belirler, SQ1/SQ2/SQ3 segmentleri "
                    "vardır). Bu ikisi aynı şey değildir ve karıştırılmaz.")
    if stype.get("has_race_distance"):
        bits.append("Yarış mesafesi var: tur=N/M gerçek yarış turudur; 'yarış temposu' "
                    "ifadesi bu seansta doğrudur.")
    return " ".join(bits)


def _sector_list(raw) -> list:
    """Sectors as an ordered list, whatever shape the feed used.

    Kept next to its only caller so the live path cannot drift from the archive
    path: both must accept a list (snapshot) and an index-keyed dict (patch).
    """
    if isinstance(raw, dict):
        try:
            keys = sorted(raw, key=lambda k: int(k))
        except (TypeError, ValueError):
            keys = sorted(raw)
        return [raw[k] for k in keys]
    if isinstance(raw, list):
        return list(raw)
    return []


# ---------- archived snapshots ----------
def _decorate(rid: int, snap, frame: dict | None = None):
    """Rebuild MED8/TREND from stored lap times and attach provenance.

    `frame` is the store.frame_row() result so the snapshot can say which writer
    produced it and which columns it cannot provide. The archive UI and the LLM
    briefing both rely on this: a row sourced from OpenF1 has no sectors and no
    race-control stream, and must say so instead of looking merely empty.

    Drivers missing from the stored frame (retired, or absent on the final lap)
    are appended from lap_times so the snapshot never silently drops them.
    """
    if not snap:
        return snap
    # The lap this frame represents comes from the FRAME (the store's primary
    # key), not from the snapshot body. Backfilled frames carry `lap: None`
    # inside their JSON -- the builder writes the number into the frames table
    # only -- so reading it from the body made `lap` 0 here, which skipped the
    # sector join entirely and left every restored session with no sector
    # columns and no colours. Live frames happened to set it, so the bug was
    # invisible until an OpenF1-sourced frame was opened.
    lap = int((frame or {}).get("lap") or snap.get("lap") or 0)
    if lap and not snap.get("lap"):
        snap["lap"] = lap
    per = store.lap_times_for(rid, lap if lap else None)
    rows = snap.get("rows") or []
    have = {str(r.get("num")) for r in rows}
    meta = store.race_driver_meta(rid)
    for num, code in sorted(meta.items()):
        if num in have:
            continue
        arr = per.get(num) or []
        if not arr:
            continue
        m = meta[num]
        rows.append({"pos": None, "num": num, "code": m.get("code") or code,
                     "grid": m.get("grid"), "name": "", "team": m.get("team") or "",
                     "gap": "", "int": "", "laps": None, "last": None, "best": None,
                     "pit": None, "inpit": False, "retired": True, "tyre": None,
                     "tyre_age": None, "stints": m.get("stints") or [], "color": ""})
    for row in rows:
        arr = per.get(str(row.get("num"))) or []
        last8 = arr[-MED_WINDOW:]
        row["med8"] = round(stats.median(last8), 3) if len(last8) >= 2 else None
        row["trend"] = round(last8[-1] - last8[0], 3) if len(last8) >= 2 else None
    snap["rows"] = rows
    snap["ok"] = True
    snap["err"] = None
    snap["archived"] = True
    snap["race_id"] = rid
    # The race ROW is authoritative for identity, not the stored frame.
    #
    # Why this override is needed even though both writers try to be correct: the
    # backfill used to hard-code session="Race" into every frame it rebuilt, so
    # Practice 1 told the briefing "seans_tipi=Yarış (race) | tur=26/26" -- a
    # 26-lap grand prix that never happened. Fixing the writer only helps frames
    # written from now on; the 68 sessions already restored would keep lying. The
    # parent row cannot be wrong here: `races.session` is what the UI, the
    # weekend ledger and the points tables all classify.
    row = store.get_race(rid) if rid else None
    if row:
        for key, col in (("meeting", "meeting"), ("official", "official"),
                         ("session", "session")):
            if row.get(col):
                snap[key] = row[col]
        if row.get("year") and not snap.get("year"):
            snap["year"] = row["year"]
        # A practice/qualifying session has no race distance. A `total` left in
        # the frame would be read as one by anyone who saw "tur=N/M".
        stype = points.session_type(row.get("session"))
        if not stype.get("has_race_distance"):
            snap["total"] = None
        snap["session_type"] = stype
    if frame:
        snap["source"] = frame.get("source")
        snap["missing"] = frame.get("missing") or []
        snap["frame_lap"] = frame.get("lap")
        snap["frame_ts"] = frame.get("ts")
    _attach_sectors(rid, snap, lap)
    return snap


def _attach_sectors(rid: int, snap: dict, lap: int) -> None:
    """Attach sector splits read from the sectors table (archive path).

    Sectors live in their own table rather than inside the frame JSON, so they
    are joined back in here. The arithmetic itself lives in _sector_stats so the
    live surface gets the identical numbers instead of a lookalike.
    """
    if not lap:
        return
    per_lap, got_lap = store.sectors_for(rid, lap)
    if not per_lap:
        # The shown lap has no sector rows, but the session does -- the "best lap"
        # columns must still be filled. Returning here (the old behaviour) left a
        # driver's best-lap sectors unavailable exactly when the current lap had
        # none, which is the common case for an in-lap or a pit lap.
        per_lap = {}
    _sector_stats(rid, snap, per_lap, sector_lap=got_lap)


def attach_live_sectors(rid: int | None, snap: dict) -> None:
    """Attach sector splits to a live snapshot (live path).

    The live feed carries Sectors per driver, but compact() drops them before the
    frame is stored, so a live snapshot does not get them for free. Reading them
    from the row keeps the live surface on the same footing as the archive:
    without this, "which sector did he lose" is answerable only for past races.
    The best-sector lookup still comes from the table, which is what makes IDEAL
    and LOSS comparable across laps.
    """
    if not snap or not snap.get("rows"):
        return
    per_lap = {}
    for row in snap.get("rows") or []:
        num = str(row.get("num"))
        # The live feed sometimes sends Sectors as an index-keyed dict instead of
        # a list; slicing that raised KeyError: slice(None, 3, None) on every
        # tick, so no live sector ever reached the briefing. Normalise here too,
        # so the live and archive paths accept the same shapes.
        secs = _sector_list(row.get("sectors"))
        vals, segs = [], []
        for s in secs[:3]:
            if not isinstance(s, dict) or s.get("Stopped"):
                vals.append(None)
                segs.append(None)
                continue
            vals.append(store._sec(s.get("Value")))
            segs.append(store._segs(s.get("Segments")))
        while len(vals) < 3:
            vals.append(None)
            segs.append(None)
        if all(v is None for v in vals) and not any(segs):
            continue
        per_lap[num] = {"s1": vals[0], "s2": vals[1], "s3": vals[2],
                        "segs": segs, "source": store.SOURCE_LIVE}
    if not per_lap:
        return
    _sector_stats(rid, snap, per_lap, sector_lap=snap.get("lap"))


_BEST_TTL = 5.0        # seconds; sector bests move only when a lap completes
_best_memo: dict[int, tuple[float, dict]] = {}


_RC_TTL = 10.0
_rc_memo: dict[int, tuple[float, list]] = {}


def stored_rc(rid) -> list[dict]:
    """Stored race-control messages for a race, memoised for a few seconds.

    Called once per SSE tick, so it must not scan the frames table every second;
    10 s is short enough that a new penalty shows up promptly and long enough that
    the cost disappears. Correctness note: this is a RECOVERY path -- the live
    buffer is authoritative while it has the message, and this fills in what a
    restart dropped.
    """
    if not rid:
        return []
    now = time.time()
    hit = _rc_memo.get(int(rid))
    if hit and now - hit[0] < _RC_TTL:
        return hit[1]
    try:
        msgs = store.rc_messages(int(rid))
    except Exception:
        msgs = hit[1] if hit else []
    _rc_memo[int(rid)] = (now, msgs)
    return msgs


def _best_cached(rid) -> dict:
    """store.sector_bests with a short TTL.

    The SSE stream rebuilds a snapshot every second, and sector_bests scans every
    stored sector of the race; without this the dashboard would re-read ~1200
    rows a second for numbers that change once a lap. Keyed by race id so the
    live race and a browsed archive race cannot serve each other's cache.
    """
    if not rid:
        return {"field": [None] * 3, "per_driver": {}}
    hit = _best_memo.get(rid)
    now = time.time()
    if hit and now - hit[0] < _BEST_TTL:
        return hit[1]
    try:
        best = store.sector_bests(rid)
    except Exception:
        return hit[1] if hit else {"field": [None] * 3, "per_driver": {}}
    _best_memo[rid] = (now, best)
    return best


def best_lap_sectors(rid: int, snap: dict | None = None) -> dict:
    """num -> the sectors of that driver's FASTEST lap, plus which lap it was.

    Only laps that actually have sectors count: a driver's quickest recorded time
    can belong to a lap whose sectors were never published, and returning that
    lap would produce an empty block with no explanation.

    "segs" travels with "sectors" because the mini-sector codes belong to a LAP,
    not to the table: showing the best lap's sector times beside the current
    lap's mini bars would pair two different laps in one cell and quietly lie.
    """
    try:
        all_laps = store.all_lap_times(rid)
        all_sec = store.all_sector_rows(rid)
    except Exception:
        return {"laps": {}, "sectors": {}, "segs": {}}
    laps: dict[str, int] = {}
    secs: dict[str, dict] = {}
    segs: dict[str, dict] = {}
    for num, per in all_laps.items():
        have = all_sec.get(num) or {}
        cand = [(t, L) for L, t in per.items()
                if t and L in have
                and all(have[L].get(k) is not None for k in ("s1", "s2", "s3"))]
        if not cand:
            continue
        t, L = min(cand)
        laps[num] = L
        secs[num] = {lap: [cell["s1"], cell["s2"], cell["s3"]]
                     for lap, cell in have.items()}
        segs[num] = {lap: cell["segs"] for lap, cell in have.items()}
    return {"laps": laps, "sectors": secs, "segs": segs}


def _sector_stats(rid, snap: dict, per_lap: dict, sector_lap=None) -> None:
    """Write s1/s2/s3/IDEAL/LOSS onto rows. Shared by both surfaces.

      ideal  -- sum of a driver's own best sector times: the lap he could have
                done by repeating his best sector everywhere.
      loss   -- actual lap time minus ideal, i.e. the time he actually lost.

    A sector the feed never published stays None; nothing is interpolated.
    """
    best = _best_cached(rid)
    field_best = best.get("field") or [None, None, None]
    per_driver = best.get("per_driver") or {}
    bl = best_lap_sectors(rid)
    best_laps, best_sectors = bl["laps"], bl["sectors"]
    best_segs = bl.get("segs") or {}
    for row in snap.get("rows") or []:
        num = str(row.get("num"))
        sec = per_lap.get(num)
        # Cleared up front, not only in the branch that finds a best lap: a row
        # decorated twice (a re-read snapshot) must not keep the previous lap's
        # mini bars next to new sector times.
        row["b_segs"] = None
        row["segs"] = None
        if not sec:
            row["s1"] = row["s2"] = row["s3"] = None
            row["s_status"] = [None, None, None]
            row["sector_partial"] = True
            sec = {}       # fall through: best-lap columns are still computed
        if sec:
            row["s1"], row["s2"], row["s3"] = sec.get("s1"), sec.get("s2"), sec.get("s3")
            row["segs"] = sec.get("segs")
        own = per_driver.get(num) or [None, None, None]
        # Three-way status per sector, the F1 convention:
        #   p (purple) = fastest in the FIELD this session
        #   g (green)  = this driver's OWN best this session
        #   y (yellow) = slower than his own best
        # Computed here, on the server, so the live table, the archive table and
        # any other surface colour the same number the same way. Deciding it in
        # the browser meant two implementations of "is this purple" that could
        # disagree with the IDEAL/LOSS maths sitting right next to them.
        #
        # Colour each sector on its own merits, the way a timing screen does.
        #
        # A short-lived version of this required the three sectors to add up to
        # `last` before colouring anything. That was wrong for a live table and
        # removed the purple entirely: the sectors on screen belong to the lap in
        # progress while `last` is the PREVIOUS lap, so the two never agree and
        # every cell went grey. A sector time, once published, is a complete
        # measurement of that sector -- it can be compared against bests on its
        # own, whether or not the rest of the lap has happened yet.
        #
        # `sector_partial` is kept for the UI: it says "this driver's lap is not
        # finished", which is why some cells show a dash and why a column of
        # yellow is not a verdict on the driver.
        if sec:
            row["s_status"] = _sector_status(row, field_best, own)
            row["sector_partial"] = not all(
                isinstance(row.get(k), (int, float)) for k in ("s1", "s2", "s3"))
        # The sectors above belong to the lap in progress (or the last one), which
        # is what a timing screen shows during a race. Once a session ends that is
        # the least useful data on screen: everybody's final lap is an in-lap, so
        # the whole column reads yellow and a purple is impossible. Measured at
        # the end of Mexico City qualifying: 23 sectors, all yellow, field best
        # 26.768 while every visible S1 was 31-44.
        #
        # So the BEST lap's own sectors travel with the row as well. The UI picks
        # between the two (see index.html render) instead of us guessing which one
        # the reader wanted.
        # NOTE: no `continue` here. An earlier version of this block returned
        # early, which silently skipped the IDEAL/LOSS computation below and left
        # both columns empty in every session -- a regression caught by comparing
        # a screenshot against the columns beside it.
        bl = (best_laps or {}).get(num)
        if bl:
            tri = (best_sectors or {}).get(num) or {}
            bvals = tri.get(bl) or [None, None, None]
            if any(v is not None for v in bvals):
                brow = {"s1": bvals[0], "s2": bvals[1], "s3": bvals[2]}
                row["bs1"], row["bs2"], row["bs3"] = bvals
                row["b_lap"] = bl
                row["b_status"] = _sector_status(brow, field_best, own)
                # The best lap's own mini-sector codes. They come from the same
                # lap as bs1..bs3 on purpose: the table pairs a sector time with
                # the strip underneath it, so the two must describe one lap.
                row["b_segs"] = ((best_segs.get(num) or {}).get(bl)
                                 or [None, None, None])
        if all(v is not None for v in own):
            row["ideal"] = round(sum(own), 3)
            last = _secs_of(row.get("last"))
            row["loss"] = round(last - row["ideal"], 3) if last else None
        else:
            row["ideal"] = row["loss"] = None
    snap["sectors"] = per_lap
    snap["sector_lap"] = sector_lap
    snap["sector_best"] = {"field": field_best, "per_driver": per_driver}
    snap["sector_source"] = next((v.get("source") for v in per_lap.values()
                                  if v.get("source")), None)


def _sector_status(row: dict, field_best: list, own: list) -> list:
    """"p" | "g" | "y" | None per sector (see _sector_stats).

    Tolerance is 1 ms: sector times arrive to three decimals, and a stored value
    that equals the best to the millisecond IS the best. Comparing floats with
    `==` would leave a genuine purple cell showing as green.
    """
    out = []
    for i, key in enumerate(("s1", "s2", "s3")):
        v = row.get(key)
        if not isinstance(v, (int, float)):
            out.append(None)
            continue
        fb = field_best[i] if i < len(field_best) else None
        ob = own[i] if i < len(own) else None
        if isinstance(fb, (int, float)) and abs(v - fb) < 1e-3:
            out.append("p")
        elif isinstance(ob, (int, float)) and abs(v - ob) < 1e-3:
            out.append("g")
        else:
            out.append("y")
    return out


def _secs_of(v) -> float | None:
    """'1:40.566' -> 100.566 ; 100.566 -> 100.566 ; '' -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        if ":" in s:
            m, rest = s.split(":", 1)
            return int(m) * 60 + float(rest)
        return float(s)
    except ValueError:
        return None


def archived_snapshot(rid: int, lap=None):
    """Stored snapshot for a race, decorated. Shared with archive.py."""
    fr = store.frame_row(rid, lap)
    if fr is None:
        return None
    return _decorate(rid, fr["snap"], fr)


def fmt_tokens(n) -> str:
    """1234 -> '1.2k' ; None -> '-'. Chat header space is scarce."""
    if n is None:
        return "-"
    n = int(n)
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def _meta_text(meta: dict) -> str:
    """The string saved next to a stored answer (chat.meta).

    Deliberately terse: this is what the transcript shows under each reply, and it
    has to stay readable in a 380 px column.
    """
    bits = []
    if meta.get("level"):
        bits.append("düşünme: " + str(meta["level"]))
    if meta.get("prompt_tokens"):
        bits.append("context " + fmt_tokens(meta["prompt_tokens"]))
    if meta.get("cached_tokens") and meta.get("prompt_tokens"):
        bits.append("cache %" + str(round(100 * meta["cached_tokens"] / meta["prompt_tokens"])))
    if meta.get("completion_tokens") is not None:
        bits.append("cevap " + str(meta["completion_tokens"]) + "/" + str(meta.get("max_tokens") or MAX_TOKENS))
    if meta.get("reasoning_tokens"):
        bits.append("rtok " + str(meta["reasoning_tokens"]))
    if meta.get("history_sent") is not None and meta.get("history_total") != meta.get("history_sent"):
        bits.append(f"geçmiş {meta['history_sent']}/{meta['history_total']}")
    if meta.get("truncated"):
        bits.append("KESİLDİ")
    if meta.get("learned"):
        bits.append("öğrendim: " + str(meta["learned"]))
    return " · ".join(bits)


# ---------- LLM ----------
def _load_key():
    """The key in use, wherever it was configured.

    Kept as a thin wrapper so nothing outside this module has to know that the
    source moved from an environment variable to the database (ai_config). The
    old env var is still honoured, so an existing deployment keeps working.
    """
    import ai_config
    return ai_config.resolve().get("api_key")


# Where the archive surface listens. The briefing quotes this address so a live
# agent asked "go look in the archive" can point at a real place instead of
# pretending it can browse.
ARCHIVE_URL = os.environ.get("F1_ARCHIVE_URL") or "http://127.0.0.1:8100/"

# No provider is hard-coded: an open-source install must not call a server its
# user did not choose. Both values come from ai_config (a profile saved in the
# settings dialog, or F1_AI_BASE / F1_AI_MODEL in the environment) on every
# call, so choosing a provider takes effect without a restart.
LLM_BASE = ""
LLM_MODEL = ""
#: Optional name of a provider-specific usage block inside the `usage` object
#: (routing + remaining balance). Empty by default -- see _answer_meta.
USAGE_KEY = (os.environ.get("F1_AI_USAGE_KEY") or "").strip()
# 700 cut real answers mid-sentence (measured: finish_reason="length" on a
# three-stint analysis). 1400 leaves room for a full list answer; the cap is still
# enforced so one runaway reply cannot cost a whole credit.
MAX_TOKENS = 1400
# Reasoning tokens are billed INSIDE completion_tokens, not on top of it, so one
# `max_tokens` covers both. Measured whether the cap is needed at all -- it is NOT:
#
#   question                                cap 16000        finish    time
#   "ikinci stint neden kotuydu"            ct=1350 rt=920   stop      39.9 s
#   "ikinci stint neden kotuydu" (repeat)   ct= 590 rt=482   stop      31.6 s
#   "tek tek pit yapsa kazanabilir miydi"   ct=4962 rt=3774  stop     132.0 s
#   "LEC'in stint 1'i neden yavasti"        ct=1350 rt=546   stop      34.0 s
#
# Every call stopped on its own. At a 3000 cap the same questions came back
# truncated with the whole budget spent on thinking and nothing written, so the
# cap was CAUSING the failure it was meant to prevent. Variance is large
# (590-4962 completion tokens for the same prompt), which is exactly why a cap
# tuned to the average cuts the tail.
#
# The cap therefore stays only as a runaway guard, set well above the observed
# maximum (4962), and latency -- not the cap -- is what bounds a call.
MAX_TOKENS_THINKING = 8000
# Compact policy for the transcript. Both numbers are visible in the UI now, so
# "the assistant forgot what we discussed" has an explanation instead of a guess.
HIST_TURNS = 6
HIST_CHARS = 2000
# "off" -> sent as reasoning_effort=none (measured: 0 reasoning tokens, ~7x faster).
REASONING_LEVELS = ("off", "minimal", "low", "medium", "high")
# Sockets: a genuine answer took 132 s (4962 tokens at ~38 tok/s), and a fuller
# one could take longer, plus the rescue path may add a second call.
THINK_TIMEOUT = 300
SYSTEM = (
    "Sen bir Formula 1 seans analistisin. Sana her mesajda seansın anlık görüntüsü "
    "(seans tipi, kalan süre, pozisyonlar, farklar, lastik bileşimi ve yaşı, son 8 tur "
    "medyanı, trend, pit sayısı, yarış kontrol mesajları) ve TÜM TURLAR bloğu -- her "
    "sürücünün her turundaki süre, stint sınırlarıyla -- verilir. Bu görüntü CANLI olabilir "
    "ya da GEÇMİŞ bir seansın kaydı olabilir. "
    "EN ÖNEMLİ KURAL -- SEANS TİPİ: 'SESSION' satırındaki seans_tipi alanı bu seansın ne "
    "olduğunu söyler: practice (antrenman), qualifying (sıralama), sprint_qualifying "
    "(sprint sıralaması), sprint (sprint yarışı) veya race (yarış). Cevabını bu tipe göre "
    "kur ve terimlerini ona uydur: antrenmanda 'yarış temposu' DEĞİL 'antrenman / uzun "
    "stint temposu' denir, çünkü antrenmanda yarış mesafesi, grid veya puan yoktur. "
    "SEANS SAYACI: yalnız race ve sprint'te 'tur=N/M' gerçek yarış turudur ve yarış mesafesi "
    "vardır. Practice ve qualifying'de 'tur' alanı bir saat tikidir (seans_sayacı); bu "
    "seanslarda yarış mesafesi, 'bitiş turu' veya 'N turluk yarış' diye bir şey YOKTUR -- "
    "böyle bir ifade kullanmak yanlış cevaptır. Bir sürücünün attığı tur sayısı SADECE POS "
    "tablosundaki LAPS kolonundan veya TÜM TURLAR bloğundaki o sürücünün kayıt sayısından "
    "okunur; seans sayacından türetilmez. "
    "SIRALAMA: qualifying ve sprint_qualifying seansları Q1/Q2/Q3 (sprint'te SQ1/SQ2/SQ3) "
    "segmentlerine ayrılır; her segment ayrı bir eleme dilimidir ve süresi bitince kapanır. "
    "Sıralamada puan verilmez, grid belirlenir ve 'stint' ya da 'yarış temposu' analizi "
    "yapılmaz; tek tur temposu konuşulur. "
    "SPRINT AYRIMI: 'Sprint' bir YARIŞTIR -- puan verir, pit stratejisi ve lastik aşınması "
    "vardır. 'Sprint Qualifying' / 'Sprint Shootout' ise bir SIRALAMA seansıdır -- puan "
    "vermez, grid belirler, SQ1/SQ2/SQ3 segmentleri vardır. Bu ikisi asla karıştırılmaz. "
    "KALAN SÜRE: görüntüde KALAN SÜRE alanı varsa 'seans bitimine ne kadar var' sorusunun "
    "cevabı odur; alan yoksa 'bu görüntüde kalan süre yok' de, uydurma. "
    "Kurallar: SADECE verilen sayılara dayan; veride olmayan bir şeyi uydurma, yoksa 'bu veri "
    "feed'de yok' de. 'Tüm turlar elinde var mı' sorusuna cevap: EVET, TÜM TURLAR bloğunda "
    "listeleniyor; MED_last8 yalnızca son 8 turun özetidir, veri sınırı değildir. Tur tur detay "
    "istenirse TÜM TURLAR bloğundan oku; 'tur tur veri yok' demek yasak (gerçekten eksikse orada "
    "'kayıt yok: tur ...' yazar). TUR BAZLI SIRALAMA bloğu her sürücünün hangi turda kaçıncı "
    "sıraya geçtiğini verir: 'X tur N'de kaçıncıydı', 'stint 1'i kaçıncı bitirdi', 'ne zaman "
    "3.'lüğe düştü' gibi sorular bu bloktan cevaplanır -- 'pozisyon kaydı verilmemiş' demek "
    "YASAK. Yalnız sıra değişimleri yazılıdır: tur N'in sırası, N veya öncesindeki son giriştir. "
    "EN İYİ TUR bloğu her sürücünün en hızlı turunu verir: sektör süreleri, i1/i2/st hızları "
    "(km/h) ve mini sektör bayrakları; S= alanı o turun kendi sektör süreleridir (POS "
    "tablosundaki sektörler GÜNCEL turundur, en iyi turunki değil -- karıştırma). "
    "İki sürücüyü karşılaştırırken sektör sürelerini sn, "
    "hızları km/s olarak kıyasla. Mini sektör bayrakları (P/S/.) SÜRE DEĞİLDİR: bunlardan "
    "'şurada 80 ms kaybetti' gibi bir sayı üretilemez; yalnız 'kim nerede mor/kırmızıydı' "
    "denir. Mini sektör süresi feed'de hiç yok. "
    "TERCİH satırındaki biçime uy: süreleri orada yazan birimde ver (mm:ss.mmm ya da saniye), "
    "istenen sunum biçimini (liste, tablo, düz cümle) ve dili kullan. Tahmin verirken mekanizmayı "
    "ve kritik sayıları (sn/tur farkı, kalan tur ya da kalan süre, lastik yaşı) yaz. Lastik "
    "aşınması ve pit stratejisi belirsizse bunu açıkça söyle. Türkçe cevap ver. Sürücüleri 3 "
    "harfli kodla (VER, LEC) an."
)


def _request_body(model, msgs, level, budget, *, stream: bool) -> dict:
    """The provider request body. One place, so the streaming and non-streaming
    paths cannot drift apart in model, budget or reasoning settings."""
    body = {"model": model, "messages": msgs, "temperature": 0.2,
            "max_tokens": budget, "stream": bool(stream)}
    lvl = (level or "").strip().lower()
    if lvl in REASONING_LEVELS:
        # "off" must be sent as "none" — the provider maps it to zero thinking tokens.
        body["reasoning_effort"] = "none" if lvl == "off" else lvl
    if stream:
        # Ask for the usage block on the final chunk. Providers that do not know
        # this option ignore it, which is why the cost numbers are read leniently
        # rather than assumed.
        body["stream_options"] = {"include_usage": True}
    return body


def _answer_meta(*, usage: dict, choice: dict, budget: int, level: str, rescued: bool,
                 ai: dict, model: str, ctx: str, sent: list, usable: list) -> dict:
    """The cost/quality block stored next to an answer. Shared by both paths."""
    u = usage or {}
    # Some OpenAI-compatible gateways add their own usage block (routing and a
    # remaining balance). Its key is deployment-specific, so it is read from
    # F1_AI_USAGE_KEY instead of being hard-coded to one provider's name: a
    # public repository should not advertise which gateway its author uses, and
    # an install without that block simply gets None here.
    extra = u.get(USAGE_KEY) if USAGE_KEY else None
    extra = extra if isinstance(extra, dict) else {}
    meta = {"level": ("off (bütçe yetmedi)" if rescued else (level or "default")),
            "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "prompt_tokens": u.get("prompt_tokens"),
            "total_tokens": u.get("total_tokens"),
            "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "max_tokens": budget,
            "truncated": (choice or {}).get("finish_reason") == "length",
            "rescued": rescued,
            "finish_reason": (choice or {}).get("finish_reason"),
            "credited_route": extra.get("routed_model"),
            # Which provider/model produced this answer. Without it, a user who
            # switched providers could not tell an old answer from a new one in
            # the same transcript.
            "provider": (ai or {}).get("label") or "",
            "model_used": model,
            "context_chars": len(ctx),
            "history_sent": len(sent),
            "history_total": len(usable)}
    try:
        # The provider reports the remaining balance; a real number is far more
        # useful than a computed guess, so pass it through untouched.
        meta["credits_cr"] = float(extra["credits_remaining_cr"])
    except (KeyError, TypeError, ValueError):
        meta["credits_cr"] = None
    return meta


async def llm_chat_stream(history, question, reasoning=None, snap=None, archived=False,
                          rid=None):
    """Stream one answer as events, so the connection is never silent.

    Why this exists: the non-streaming call returns nothing for 30-110 s while the
    model thinks, and any proxy in front of this app drops a quiet connection,
    often at ~100 s, answering with its own HTML error page -- which the browser
    reports as "HTTP 524 — sunucu JSON yerine HTML döndürdü". Measured:
    a "high" reasoning question sat silent past that limit and the answer was lost
    even though the provider had produced it (and charged for it).

    Streaming fixes the cause rather than the symptom: tokens arrive continuously,
    so the connection carries bytes the whole time. The endpoint in front of this
    generator additionally sends an SSE comment every few seconds while the model
    is still thinking, because reasoning tokens are NOT sent as content -- without
    that heartbeat a thinking-heavy question would still go quiet.

    Yields dicts:
        {"type": "start",  "level": str, "model": str}
        {"type": "thinking", "chars": int}      reasoning text is not shown, only counted
        {"type": "delta",  "text": str}         answer text, append as it arrives
        {"type": "done",   "answer": str, "meta": dict}
        {"type": "error",  "message": str}
    """
    import ai_config
    ai = ai_config.resolve()
    key = ai.get("api_key")
    if not key:
        yield {"type": "error", "message":
               "LLM anahtarı tanımlı değil. Asistan başlığındaki ⚙ düğmesinden kendi "
               "sağlayıcını, modelini ve anahtarını girebilirsin."}
        return
    if not snap:
        yield {"type": "error", "message": "Bu istek için veri görüntüsü yok."}
        return

    base = (ai.get("base_url") or LLM_BASE).rstrip("/")
    model = ai.get("model") or LLM_MODEL
    if not base or not model:
        yield {"type": "error", "message":
               "Sağlayıcı adresi ya da model tanımlı değil. Asistan başlığındaki ⚙ "
               "düğmesinden base URL, model ve anahtarı gir."}
        return
    p = prefs()
    reasoning = reasoning or p.get("reasoning") or None
    s = dict(snap)
    s["archived"] = bool(archived or s.get("archived"))
    ctx = context_text(s, p)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "system", "content": "VERİ:\n" + ctx}]
    usable = [h for h in (history or [])
              if h.get("role") in ("user", "assistant") and h.get("content")]
    sent = usable[-HIST_TURNS:]
    for h in sent:
        msgs.append({"role": h["role"], "content": h["content"][:HIST_CHARS]})
    msgs.append({"role": "user", "content": question[:2000]})

    lvl = (reasoning or "").strip().lower()
    budget = MAX_TOKENS_THINKING if (lvl in REASONING_LEVELS and lvl != "off") else MAX_TOKENS
    yield {"type": "start", "level": lvl or "default", "model": model}

    parts: list[str] = []
    usage: dict = {}
    choice: dict = {}
    rescued = False

    async for ev in _stream_once(base, key, model, msgs, lvl, budget, parts):
        if ev.get("type") == "usage":
            usage = _merge_usage(usage, ev.get("usage") or {})
            continue
        if ev.get("type") == "choice":
            choice = ev.get("choice") or {}
            continue
        # NOT appended here: `_stream_once` already accumulates into this same
        # list. Appending in both places doubled every answer (measured: 28
        # characters of deltas produced a 56-character answer).
        yield ev

    # A thinking answer that spent its whole budget before writing anything is the
    # worst outcome: the user waited the full time and gets "(boş cevap)". Retry
    # once with thinking off -- measured 0 reasoning tokens, a couple of seconds.
    if not "".join(parts).strip() and (choice.get("finish_reason") == "length"):
        rescued = True
        parts = []
        yield {"type": "phase", "phase": "rescue"}
        async for ev in _stream_once(base, key, model, msgs, "off", MAX_TOKENS, parts):
            if ev.get("type") == "usage":
                usage = _merge_usage(usage, ev.get("usage") or {})
                continue
            if ev.get("type") == "choice":
                choice = ev.get("choice") or {}
                continue
            yield ev

    answer = "".join(parts).strip() or "(boş cevap)"
    meta = _answer_meta(usage=usage, choice=choice, budget=budget, level=lvl,
                        rescued=rescued, ai=ai, model=model, ctx=ctx,
                        sent=sent, usable=usable)
    if rid is not None:
        try:
            store.usage_add(rid, meta)
        except Exception as e:
            print("usage: kayıt başarısız:", e)
    yield {"type": "done", "answer": answer, "meta": meta}


async def _stream_once(base: str, key: str, model: str, msgs: list, level: str,
                       budget: int, parts: list):
    """One streaming call. Yields provider events rewritten as our own.

    The socket read is synchronous, so it runs in a worker thread and hands chunks
    to the event loop through a queue: reading inline would freeze the whole
    surface for the length of the answer (the same failure the non-streaming path
    documents).
    """
    body = _request_body(model, msgs, level, budget, stream=True)
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"})
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def pump() -> None:
        try:
            with urllib.request.urlopen(req, timeout=THINK_TIMEOUT) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":"):
                        continue          # SSE comment / blank separator
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, ("chunk", json.loads(payload)))
                    except json.JSONDecodeError:
                        continue
            loop.call_soon_threadsafe(q.put_nowait, ("eof", None))
        except Exception as e:                       # noqa: BLE001 - reported to the user
            loop.call_soon_threadsafe(q.put_nowait, ("err", e))

    task = asyncio.create_task(asyncio.to_thread(pump))
    thinking_chars = 0
    thinking_sent = 0
    try:
        while True:
            kind, payload = await q.get()
            if kind == "eof":
                break
            if kind == "err":
                e = payload
                import ai_config as _ac
                yield {"type": "error",
                       "message": _ac.scrub(
                           f"LLM hatası: {type(e).__name__}: {e}", key)[:400]}
                return
            d = payload
            u = d.get("usage")
            if u:
                yield {"type": "usage", "usage": u}
            choices = d.get("choices") or []
            if not choices:
                continue
            ch = choices[0] or {}
            delta = ch.get("delta") or {}
            # Reasoning text is counted, not shown: it is the model's scratchpad,
            # and showing it would put unverified prose in front of the user.
            reason = delta.get("reasoning_content") or delta.get("reasoning")
            if reason:
                thinking_chars += len(reason)
                # Throttled: the provider sends a reasoning chunk every few
                # tokens, so forwarding each one produced hundreds of SSE events
                # per answer. 200 characters is frequent enough that the panel's
                # "düşünüyor…" counter moves visibly, and ~50x quieter on the wire.
                # The purpose of these events is liveness, not fidelity.
                if thinking_chars - thinking_sent >= 200:
                    thinking_sent = thinking_chars
                    yield {"type": "thinking", "chars": thinking_chars}
            text = delta.get("content")
            if text:
                parts.append(text)
                yield {"type": "delta", "text": text}
            if ch.get("finish_reason"):
                yield {"type": "choice", "choice": ch}
    finally:
        if not task.done():
            task.cancel()


async def llm_chat(history, question, reasoning=None, snap=None, archived=False,
                   rid=None):
    import ai_config
    ai = ai_config.resolve()
    key = ai.get("api_key")
    if not key:
        # Actionable, not vague: the old message named an env var the user could
        # not set from the UI. The UI now exists, so point at it.
        return ("LLM anahtarı tanımlı değil. Asistan başlığındaki ⚙ düğmesinden kendi "
                "sağlayıcını, modelini ve anahtarını girebilirsin."), {"error": True, "no_key": True}
    base = (ai.get("base_url") or LLM_BASE).rstrip("/")
    model = ai.get("model") or LLM_MODEL
    if not base or not model:
        return ("Sağlayıcı adresi ya da model tanımlı değil. Asistan başlığındaki ⚙ "
                "düğmesinden base URL, model ve anahtarı gir."), {
                    "error": True, "no_key": True}
    if not snap:
        return "Bu istek için veri görüntüsü yok.", {"error": True}
    p = prefs()
    # A persisted "düşünme = kapalı" preference must survive a client that forgets
    # to send the field; an explicit per-request value always wins.
    reasoning = reasoning or p.get("reasoning") or None
    s = dict(snap)
    s["archived"] = bool(archived or s.get("archived"))
    ctx = context_text(s, p)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "system", "content": "VERİ:\n" + ctx}]
    # Compact: the transcript is silently trimmed to the last HIST_TURNS messages
    # and each message to HIST_CHARS. The counts are reported back so the badge can
    # say "6/18 mesaj gönderildi" instead of the trimming being invisible.
    usable = [h for h in (history or [])
              if h.get("role") in ("user", "assistant") and h.get("content")]
    sent = usable[-HIST_TURNS:]
    for h in sent:
        msgs.append({"role": h["role"], "content": h["content"][:HIST_CHARS]})
    msgs.append({"role": "user", "content": question[:2000]})
    lvl = (reasoning or "").strip().lower()
    thinking = lvl in REASONING_LEVELS and lvl != "off"
    budget = MAX_TOKENS_THINKING if thinking else MAX_TOKENS
    body = _request_body(model, msgs, lvl, budget, stream=False)

    async def _post(b: dict) -> dict:
        req = urllib.request.Request(
            base + "/chat/completions", data=json.dumps(b).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})

        def do() -> dict:
            # Thinking answers legitimately run 70-110 s here, and the
            # empty-answer rescue may make a second call, so the socket timeout
            # has to cover both. A tight 120 s cut off real answers mid-thought
            # (measured).
            with urllib.request.urlopen(req, timeout=THINK_TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        # urlopen is synchronous. Called directly from an async handler it blocks
        # the whole event loop for the entire answer -- 20-110 s during which
        # /api/stream, /api/races and every other request on the surface are
        # frozen. Any proxy in front of the surface showed that up as 502s and
        # an HTML error page where the chat answer belonged. to_thread moves the wait
        # off the loop; the loop stays free to serve everyone else.
        return await asyncio.to_thread(do)

    try:
        d = await _post(body)
        ch = (d.get("choices") or [{}])[0]
        choice = ch.get("message", {}) or {}
        u = d.get("usage") or {}
        truncated = (ch.get("finish_reason") == "length")
        # A thinking answer that ran out of budget before writing anything is the
        # worst outcome: it costs the full wait, stores "(boş cevap)" in the
        # transcript (where later turns learn from it), and the user has nothing to
        # resume. Retrying once with thinking disabled always produces an answer --
        # measured 0 reasoning tokens, ~2 s -- so do that instead of returning
        # nothing, and say so in meta so the badge is honest about the downgrade.
        rescued = False
        if truncated and not (choice.get("content") or "").strip():
            rescued = True
            retry = dict(body)
            retry["reasoning_effort"] = "none"
            retry["max_tokens"] = MAX_TOKENS
            d2 = await _post(retry)
            ch2 = (d2.get("choices") or [{}])[0]
            if (ch2.get("message", {}) or {}).get("content"):
                ch, choice = ch2, ch2.get("message") or {}
                # Keep the first call's cost: both were paid for.
                u = _merge_usage(u, (d2.get("usage") or {}))
                truncated = (ch.get("finish_reason") == "length")
            else:
                u = _merge_usage(u, (d2.get("usage") or {}))
        meta = _answer_meta(usage=u, choice=ch, budget=budget, level=lvl,
                            rescued=rescued, ai=ai, model=model, ctx=ctx,
                            sent=sent, usable=usable)
        if rid is not None:
            try:
                store.usage_add(rid, meta)
            except Exception as e:
                print("usage: kayıt başarısız:", e)
        return (choice.get("content") or "(boş cevap)"), meta
    except Exception as e:
        import ai_config as _ac
        return _ac.scrub(f"LLM hatası: {type(e).__name__}: {e}", key)[:400], {
            "level": lvl or "default", "error": True}


def _merge_usage(a: dict, b: dict) -> dict:
    """Sum two usage blocks, keeping the newest provider extras.

    A rescued request is two calls against one answer, so the tokens reported to
    the UI must be the sum -- otherwise the credits line would understate what the
    question actually cost.
    """
    out = dict(a or {})
    for k in ("completion_tokens", "prompt_tokens", "total_tokens"):
        try:
            out[k] = int((a or {}).get(k) or 0) + int((b or {}).get(k) or 0)
        except (TypeError, ValueError):
            pass
    det = dict((a or {}).get("completion_tokens_details") or {})
    for k, v in ((b or {}).get("completion_tokens_details") or {}).items():
        try:
            det[k] = int(det.get(k) or 0) + int(v or 0)
        except (TypeError, ValueError):
            det[k] = v
    out["completion_tokens_details"] = det
    for k in [x for x in ("prompt_tokens_details", USAGE_KEY) if x]:
        if (a or {}).get(k) or (b or {}).get(k):
            out[k] = (b or {}).get(k) or (a or {}).get(k)
    return out
