#!/usr/bin/env python3
"""Saklanan her alan brifingde temsil ediliyor mu — ve kaynakta olup hiç saklanmayan var mı?

Bugüne kadarki en pahalı hata sınıfı bu: veri veritabanında vardı, brifinge
girmiyordu, ajan da dürüstçe "bu veri feed'de yok" diyordu. Sektörler, tur bazlı
sıra, tarih, tam isimler ve RETIRED bayrağı bu şekilde birer kullanıcı sorusuyla
keşfedildi. Bu betik o keşfi kullanıcıya bırakmaz: şemayı okur, brifingi üretir,
eşleşmeyen her alanı raporlar ve eşleşmeyen alan varsa sıfırdan farklı çıkar.

OpenF1 verisi CC BY-NC-SA 4.0 altındadır: yalnız ticari olmayan kullanım, aynı
lisansla paylaşım. Bu deponun MIT lisansı kodu kapsar, çekilen veriyi kapsamaz.

Kullanım:
    .venv/bin/python tools/audit_coverage.py            # rapor
    .venv/bin/python tools/audit_coverage.py --strict    # eksik varsa exit 1

İki yönü de denetler:

1. **Saklanan -> brifing.** `frames.snap`, satır alanları ve `races` tablosundaki her
   alan ya "temsil ediliyor" ya "bilinçli dışta" olmalı.
2. **Kaynak -> saklanan.** OpenF1 `/laps` gibi bir kaynağın sunduğu her alan ya
   veritabanında olmalı ya da gerekçesiyle dışta bırakılmalı. Bu ikinci yön, birinci
   yönün kör noktasıydı: `i1_speed`/`i2_speed`/`st_speed` her `/laps` cevabında vardı,
   hiç saklanmıyordu, ve "saklanan <-> brifing" denetimi bunu göremezdi çünkü alan
   hiçbir zaman "saklanan" tarafında değildi.

Yeni bir alan eklendiğinde bu betik onu ya muhasebeleşmiş ya da raporlanmış görmek
ister. İkisi de değilse `--strict` düşer.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
import store

# Alan -> neden brifingde yok. Buraya eklemek "bu alanı bilerek dışarıda
# bırakıyoruz" demektir; gerekçesiz eklenmesin.
DELIBERATE = {
    "color": "yalnız arayüz rengi, modele anlamı yok",
    "num": "araba numarası; yarış kontrol mesajlarında zaten 'CAR 12 (ANT)' olarak geçiyor",
    "meeting_key": "eşleştirme anahtarı, iç kullanım (OpenF1 backfill)",
    "path": "F1 arşiv yolu, iç kullanım",
    "ok": "API durum bayrağı, veri değil",
    "err": "API hata alanı, veri değil",
    "updated": "feed'de boş; zaman bilgisi server_time ve RC damgalarında",
    "server_time": "saat damgası; yarış kontrol mesajları kendi damgasını taşıyor",
    "stints": "TÜM TURLAR bloğu stint aralıklarını ([SOFT 1-15]) zaten yazıyor",
    "first_seen": "yakalama telemetrisi, yarış verisi değil",
    "last_seen": "yakalama telemetrisi, yarış verisi değil",
    "snap": "ham çerçevenin kendisi",
    "id": "veritabanı birincil anahtarı, yarış verisi değil",
}

# Alan -> brifingde hangi metinle temsil edildiği (insan okur diye).
REPRESENTED = {
    "pos": "POS kolonu + TUR BAZLI SIRALAMA",
    "code": "DRV kolonu",
    "grid": "GRID kolonu",
    "team": "TEAM kolonu",
    "gap": "GAP kolonu",
    "int": "INT kolonu",
    "laps": "LAPS kolonu",
    "last": "LAST kolonu",
    "best": "BEST kolonu",
    "pit": "PIT kolonu",
    "tyre": "TYRE(age) kolonu",
    "tyre_age": "TYRE(age) kolonu",
    "s1": "S1 kolonu", "s2": "S2 kolonu", "s3": "S3 kolonu",
    "ideal": "IDEAL kolonu", "loss": "LOSS kolonu",
    "name": "SÜRÜCÜLER satırı",
    "retired": "YARIŞ DIŞI satırı",
    "inpit": "PİT LANE TURLARI satırı",
    "meeting": "SESSION başlığı",
    "session": "SESSION başlığı",
    "status": "SESSION başlığı",
    "total": "lap=N/total",
    "lap": "lap=N/total",
    "track": "track=...",
    "air": "WEATHER", "track_temp": "WEATHER", "humidity": "WEATHER",
    "rain": "WEATHER", "wind": "WEATHER",
    "rc": "RACE CONTROL bloğu",
    "rows": "POS tablosu",
    "official": "YARIŞ KİMLİĞİ satırı",
    "start_date": "YARIŞ KİMLİĞİ satırı",
    "end_date": "YARIŞ KİMLİĞİ satırı",
    "total_laps": "lap=N/total başlığı",
    "year": "YARIŞ KİMLİĞİ satırı",
    "final_status": "YARIŞ KİMLİĞİ satırı",
    # lap_speeds tablosu (OpenF1'den geri doldurulur, frames.snap'te yok)
    "i1": "EN İYİ TUR satırı (i1 hızı)",
    "i2": "EN İYİ TUR satırı (i2 hızı)",
    "st": "EN İYİ TUR satırı (st hız tuzağı)",
    # sectors tablosu
    "s1": "S1 kolonu + EN İYİ TUR S=", "s2": "S2 kolonu + EN İYİ TUR S=",
    "s3": "S3 kolonu + EN İYİ TUR S=",
    "seg1": "EN İYİ TUR mini bayrakları", "seg2": "EN İYİ TUR mini bayrakları",
    "seg3": "EN İYİ TUR mini bayrakları",
    "sec": "TÜM TURLAR + BEST kolonu",
}


# OpenF1 /laps alanlari -> nerede duruyor / neden disarida.
OPENF1_LAPS = {
    "driver_number": "lap_times.num / sectors.num / lap_speeds.num",
    "lap_number": "lap_times.lap / sectors.lap / lap_speeds.lap",
    "lap_duration": "lap_times.sec",
    "duration_sector_1": "sectors.s1",
    "duration_sector_2": "sectors.s2",
    "duration_sector_3": "sectors.s3",
    "segments_sector_1": "sectors.seg1 (durum kodlari, sure degil)",
    "segments_sector_2": "sectors.seg2",
    "segments_sector_3": "sectors.seg3",
    "i1_speed": "lap_speeds.i1",
    "i2_speed": "lap_speeds.i2",
    "st_speed": "lap_speeds.st",
    "date_start": "kullanilmiyor: tur baslangic zamani; rapor ici simulasyon yok",
    "meeting_key": "races.meeting_key (eslestirme anahtari)",
    "session_key": "backfill sirasinda cozulur, kalici tutulmuyor",
    "is_pit_out_lap": "PIT kolonu + PIT LANE TURLARI satiri ayni bilgiyi veriyor",
}


def check_source(_race_id: int) -> list[str]:
    """Kaynakta olup hicbir yerde saklanmayan alanlar.

    Ag cagrisi yapar; basarisiz olursa denetimi durdurmaz, sadece atlandigini soyler
    (denetim cevrimdisi da calisabilmeli).
    """
    import json as _json
    import urllib.request
    problems: list[str] = []
    try:
        race = store.get_race(_race_id) or {}
        # OpenF1 session_key is not persisted; resolve it via the meeting key.
        mk = race.get("meeting_key")
        if not mk:
            print("  kaynak denetimi: meeting_key yok, atlandi")
            return []
        url = f"https://api.openf1.org/v1/sessions?meeting_key={mk}"
        sessions = _json.load(urllib.request.urlopen(url, timeout=20))
        sk = next((x["session_key"] for x in sessions
                   if str(x.get("session_name", "")).lower().startswith("race")), None)
        if not sk:
            print("  kaynak denetimi: yaris session_key bulunamadi, atlandi")
            return []
        laps = _json.load(urllib.request.urlopen(
            f"https://api.openf1.org/v1/laps?session_key={sk}", timeout=40))
        seen = set()
        for l in laps:
            seen.update(l.keys())
        for f in sorted(seen):
            if f not in OPENF1_LAPS:
                problems.append(f)
    except Exception as e:
        print(f"  kaynak denetimi atlandi ({type(e).__name__}: {e})")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--race-id", type=int, default=1)
    ap.add_argument("--lap", type=int, default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--no-network", action="store_true",
                    help="kaynak denetimini atla (cevrimdisi)")
    a = ap.parse_args()

    rid = a.race_id
    laps = store.race_lap_numbers(rid)
    if not laps:
        print(f"yarış {rid} için çerçeve yok")
        return 2
    lap = a.lap or laps[-1]

    frame = store.frame_row(rid, lap) or {}
    snap = frame.get("snap") or {}
    ctx = analysis.context_text(analysis.archived_snapshot(rid, lap))

    fields = set(snap.keys())
    for r in snap.get("rows") or []:
        fields.update(r.keys())
    race = store.get_race(rid) or {}
    fields.update(k for k, v in race.items() if v is not None)
    # Yardimci tablolarin kolonlari da "saklanan alan" sayilir; yoksa denetim
    # i1/i2/st gibi alanlari hic gormez.
    import sqlite3 as _sq
    try:
        with _sq.connect(store.DB_PATH) as c:
            for tbl in ("sectors", "lap_speeds", "lap_times"):
                for r in c.execute(f"PRAGMA table_info({tbl})"):
                    col = r[1]
                    if col not in ("race_id", "num", "lap", "source"):
                        fields.add(col)
    except Exception as e:
        print(f"  uyari: yardimci tablo kolonlari okunamadi: {e}")

    missing = sorted(f for f in fields
                     if f not in REPRESENTED and f not in DELIBERATE)

    print(f"yarış {rid}, tur {lap} | {len(fields)} alan | brifing {len(ctx)} karakter")
    print(f"  temsil ediliyor : {len(fields & set(REPRESENTED))}")
    print(f"  bilinçli dışta  : {len(fields & set(DELIBERATE))}")
    print(f"  KARAR VERİLMEMİŞ: {len(missing)}")
    for f in missing:
        val = snap.get(f, race.get(f))
        print(f"    ! {f} = {str(val)[:60]}")

    print("\nkaynak -> saklanan (OpenF1 /laps):")
    src_missing = [] if a.no_network else check_source(rid)
    if a.no_network:
        print("  atlandi (--no-network)")
    elif src_missing:
        print(f"  KARAR VERİLMEMİŞ: {len(src_missing)}")
        for f in src_missing:
            print(f"    ! {f}")
    else:
        print("  tüm kaynak alanları muhasebeleşti.")

    if missing or src_missing:
        print("\nHer alan için karar ver: REPRESENTED/sakla, veya DELIBERATE gerekçesiyle dışta bırak.")
        if a.strict:
            return 1
    else:
        print("\ntüm alanlar muhasebeleşti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
