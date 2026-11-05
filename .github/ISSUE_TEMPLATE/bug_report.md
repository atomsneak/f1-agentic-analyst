---
name: Bug report
about: Bir şey çalışmıyor — davranış yanlış
title: ''
labels: bug
assignees: ''
---

## Ne oluyor

<!-- Tek cümle. Belirti, yorum değil. -->

## Nasıl tekrarlanır

<!-- Kopyalanabilir komut. Hangi yüzey (8099/8100/8101/8103), hangi yarış/seans yüklü. -->

```bash

```

## Beklenen / gerçek

| | |
|---|---|
| Beklenen | |
| Gerçek | |

## Ham veriyle kontrol

<!-- Cevap "veri yok" diyorsa veri gerçekten yok mu? Cevap bir sayı veriyorsa doğru mu?
     Doğru kaynak: data/f1dash.db (sqlite3 ile salt-okunur). Chat cevabı kanıt sayılmaz. -->

```bash
sqlite3 -readonly data/f1dash.db "..."
```

## Tekrarlanabilirlik

- [ ] Her seferinde oluyor (…/… koşu)
- [ ] Aralıklı (…/… koşu)
- [ ] Bir kez gördüm, tekrarlayamadım

<!-- Aralıklıysa kaç koşuda kaç kez — "aralıklı" tek başına bilgi değil. -->

## Kapsam

<!-- Neyin dokunulmaması gerekiyor? Veri mi, kart API'si mi, sadece sunum mu? -->
