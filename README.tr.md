# F1 Agentic Analyst

[English](README.md) · **Türkçe**

Üç yüzey, tek arşiv.

> **Kendi anahtarınla, kendi makinende.** Bu proje hiçbir sunucuya, telemetriye ya
> da yazarın altyapısına bağlanmaz. AI sağlayıcısını ve API anahtarını sen
> girersin; anahtar yalnızca kendi makinendeki `data/f1dash.db` dosyasında
> (0600) durur, tarayıcıya geri dönmez ve dışarı gönderilmez. Gömülü bir
> sağlayıcı yoktur — kurulum, sen ayarlayana kadar AI konusunda tamamen etkisizdir.
> Ayrıntı: [AI sağlayıcı seçimi](#ai-sağlayıcı-seçimi), [SECURITY.md](SECURITY.md).

| Yüzey | Adres | Ne için | Yazar mı? |
|---|---|---|---|
| **Canlı** (`server.py`) | http://127.0.0.1:8099 | Yarış anında: sıralama, farklar, lastik, radyo mesajları, AI sohbeti, **geçici puan durumu** | Evet — feed'i kaydeder |
| **Arşiv** (`archive.py`) | http://127.0.0.1:8100 | Yarış bittikten sonra: strateji şeridi, fark grafiği, tur süresi dağılımı, sohbet | Sadece sohbet |
| **Puan Durumu** (`standings_app.py`) | http://127.0.0.1:8101 | Sezonun **resmî** sürücü ve marka sıralaması; ağ yoksa son anlık görüntü | Sadece sıralama önbelleği |

Yüzeyleri ayırdım çünkü sorulan soru farklı: canlıda "şu an ne oluyor" (tablo doğru şekil),
arşivde "ne oldu, neden" (grafik doğru şekil), puan durumunda "sezon nerede" (sıralama
doğru şekil). **Veriyi ayırmadım** — iki ayrı veritabanı birbiriyle asla uzlaşamayacak iki
doğru üretirdi. Üçü de `data/f1dash.db` dosyasını kullanır; yazma yetkisi canlı tarafta,
arşiv salt okunur, puan durumu yalnızca kendi önbellek tablolarına yazar.

Üç yüzey **ortak bir kabuk** paylaşır: `static/theme.css` (token'lar + nav + rozetler +
uyarı bileşenleri) ve `static/shell.js` (yüzeyler arası bağlantılar, biçimlendiriciler).
Her sayfanın kendi `<style>` bloğu yalnızca o sayfaya özgü kuralları taşır; `--bg`,
`--panel`, `--accent` gibi token'lar tek yerde durur. Arayüz ilk hâlini bir tasarım
prototipinden aldı; prototip görselleri **depoda tutulmaz** (içlerinde gerçek yarış
verisi vardı), yalnız CSS token'ları kaldı.

## Dosyalar

```
server.py              canlı yakalama + canlı arayüz API'si (tek yazar)
archive.py             arşiv tarayıcısı (salt okunur + sohbet)
standings_app.py       puan durumu yüzeyi (önbellekten okur, ağ yoksa da açılır)
welcome_app.py         hafta sonu yüzeyi: hafta sonu seçimi + seans kartları
weekend.py             bir hafta sonu, tek hesap: kart ızgarası + brifing özeti
scope.py               kapsam çözücü: hangi seanslar bağlama girecek (@kısayol + çipler)
chat_request.py        tek sohbet hattı — iki yüzey de burayı çağırır
ai_config.py           AI sağlayıcı profilleri (base_url, model, anahtar) + /api/ai
penalties.py           yarış kontrolünden ceza/inceleme ayrıştırma (saf fonksiyon)
store.py               ortak veritabanı katmanı — tüm SQL burada
analysis.py            ortak analiz: MED8/TREND, sektör, provenance, LLM brifingi
routing.py             "geçmiş tur" / "başka yarış" sorularını LLM'den önce çözer
points.py              puan tabloları + geçici tablo projeksiyonu (saf fonksiyonlar)
standings.py           Jolpica çekimi (urllib), normalizasyon, SQLite önbelleği
static/theme.css       ortak tema: token'lar, shell, nav, notice/strip
static/shell.js        ortak kabuk davranışı + biçimlendiriciler + SW kaydı
static/sw.js           service worker (önbellek şablonu; pwa.py doldurur)
static/icons/          PWA ikon seti (192/512/maskable/apple/favicon)
pwa.py                 dört yüzeye ortak PWA kablolaması (manifest + /sw.js)
tools/make_icons.py    design/pwa-icon.svg -> static/icons/*.png
index.html             canlı arayüz
archive.html           arşiv arayüzü
standings.html         puan durumu arayüzü
welcome.html           hafta sonu arayüzü (kart ızgarası)
tests/test_points.py   puan hesabı birim testleri
tests/ui_test.js       tarayıcı tarafı davranış testleri (panel, klavye, fetchJSON)
tests/layout_test.js   gerçek tarayıcıda düzen ölçümü (chrome-headless-shell)
tests/test_pwa.py      manifest/precache/kaynak testleri (sunucusuz)
tests/test_weekend.py  hafta sonu defteri + kart durumu + özet testleri
tests/welcome_test.js  hafta sonu arayüzü davranış testleri (DOM taklidi)
tests/test_scope.py    kapsam çözümü (@kısayollar, çipler, belirsizlik)
tests/test_ai_config.py sağlayıcı profilleri, sır sızdırmama, öncelik sırası
tests/test_stream.py   akışlı sohbet: istek gövdesi, meta paritesi, SSE ayrıştırıcı
tests/test_penalties.py ceza ayrıştırma: çekildi/inceleme/çoklu araç ayrımı
tests/pwa_test.js      gerçek tarayıcıda kurulum + çevrimdışı açılış
tools/shot.js          CDP ile ekran görüntüsü aracı
tests/smoke_real_db.py gerçek DB kopyasıyla duman testi
tools/backfill_openf1.py  geçmiş yarışı OpenF1 aynasından tamamla
tools/audit_coverage.py brifing kapsam denetimi: saklanan her alan temsil ediliyor mu, eksik var mı
tools/fix_sector_laps.py yanlış tur numarasına yazılmış sektör satırlarını onar
data/f1dash.db         tek SQLite dosyası (WAL)
design/pwa-icon.svg    uygulama ikonunun tek kaynağı (make_icons.py -> static/icons/)
```

Hiçbir modülde ham SQL tekrarlanmaz: `server.py`, `archive.py`, `standings_app.py` ve
`welcome_app.py` sadece
`store.py` fonksiyonlarını çağırır. MED8/TREND hesabı ve LLM brifingi `analysis.py`'de,
puan aritmetiği `points.py`'de tek yerde durur, böylece yüzeyler aynı veriye aynı anlamı
yükler.

## Hızlı başlangıç

Gereken tek şey Python 3.10+ ve üç paket. Node, veritabanı sunucusu, derleme
adımı yok — SQLite dosyası ilk çalıştırmada kendini kurar.

```bash
git clone https://github.com/atomsneak/f1-agentic-analyst.git
cd f1-agentic-analyst
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

./run.sh            # canlı  http://127.0.0.1:8099
./run.sh archive    # arşiv  http://127.0.0.1:8100
./run.sh standings  # puan   http://127.0.0.1:8101
./run.sh welcome    # hafta sonu http://127.0.0.1:8103
```

Ardından sohbet başlığındaki **⚙ AI** düğmesinden kendi sağlayıcını gir:

| alan | örnek |
|---|---|
| Base URL | `https://api.openai.com/v1`, `https://openrouter.ai/api/v1`, `http://127.0.0.1:11434/v1` |
| Model | `gpt-4o-mini`, `anthropic/claude-sonnet-4`, `llama3.1:8b` |
| API anahtarı | kendi anahtarın — yalnız bu makinede saklanır |

Üç değer de zorunludur ve **hiçbiri kodda gömülü değildir**: depo, sen
ayarlayana kadar AI konusunda etkisizdir. Yerel model (Ollama, llama.cpp,
LM Studio) kullanacaksan anahtar alanına sağlayıcının kabul ettiği herhangi bir
değer yeterlidir; `base_url` için loopback serbesttir.

Testler:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'   # 203 test
node tests/ui_test.js                                          # 79 test
```

`tests/layout_test.js` ve `tests/pwa_test.js` gerçek headless Chrome ister;
yoksa atlanır (`tests/layout_test.js` başındaki kurulum notuna bak).

## Servisler

```bash
systemctl --user status f1-live f1-archive f1-standings f1-welcome
systemctl --user restart f1-live            # kod değişince
journalctl --user -u f1-live -f             # canlı log
```

`Restart=always` + `StartLimitIntervalSec=0`: yarış ortasında süreç ölürse (OOM, ağ,
beklenmedik istisna) 3 saniye içinde geri gelir ve ısrarla denemeye devam eder.
Loglar journald'e gider, disk kullanımını journald kendi sınırlar — ayrı logrotate yok.
`Linger=yes` açık olduğu için makine yeniden başlayınca giriş yapmadan da ayağa kalkar.

Elle çalıştırmak gerekirse: `./run.sh`, `./run.sh archive`, `./run.sh welcome`,
`./run.sh backfill --list`.

### Dört yüzey, dört port

Her yüzey kendi portunda, kök yolda ayrı bir süreçtir. Bu sürümde yol öneki
yönlendiricisi yoktur:

| Port | Yüzey |
|---|---|
| 8103 | hafta sonu (seans kartları) — iniş sayfası |
| 8099 | canlı timing |
| 8100 | arşiv (salt okunur + sohbet) |
| 8101 | puan durumu |

Ayrı süreç olmaları bilinçli: birleşik bir süreçte arşivin CPU'ya binen grafik
hesabı ya da puan durumunun 15 sn'lik HTTP zaman aşımı, feed'i okuyan döngüyle
aynı event loop'u paylaşırdı. Ayrı süreçlerde bu imkânsız.

`static/shell.js` portu URL'ye çeviren tek yer: her nav bağlantısı `data-port`
bildirir, kabuk da sayfanın sunulduğu makine adını yerine koyar. Böylece aynı
işaretleme hem `127.0.0.1`'de hem bir LAN adresinde çalışır.

## Mobil yerleşim (<=640px)

Dört yüzeyin dördünde de telefon kuralları var; hepsi sayfa içi `<style>` bloğunda,
`static/theme.css` yalnız ortak parçaları taşır. (Hafta sonu ızgarasının mobil
kuralları `welcome.html` içinde: iki sütun kart, ≥44px dokunma hedefi.)

**Kolon gizleme — neyin gittiği gerekçesiyle birlikte:**

| sayfa | toplam kolon | telefonda kalan | giden |
|---|---|---|---|
| canlı | 13 | 5 (P, DRV, GRID, GAP, TYRE) | TEAM, INT, LAP, LAST, BEST, MED8, TREND, PIT |
| arşiv | 15 | 9 (P, sürücü, fark, lastik, S1–S3, ideal, kayıp) | TEAM, INT, LAP, LAST, BEST, PIT |
| puan durumu | 6 | 3 (sıra, sürücü, puan) | TEAM, WINS, GAP |

Canlı sayfada takım adı gidiyor çünkü renk çubuğu sürücünün yanında zaten var.
Arşivde Son/En iyi gidiyor çünkü İdeal ve Kayıp onları özetliyor
(`kayıp = gerçek - ideal`). Puan durumunda sürücü adı iki satıra açılıyor,
takım adı yerine o kalıyor.

**Ölçülen genişlikler** (390px ekran, kullanılabilir ~360px; karakter
genişliklerinden hesaplandı, tarayıcıda doğrulanmadı):

- arşiv, 11 kolon: ~673px → sığmaz
- arşiv, 9 kolon: ~469px → hâlâ sığmaz, kart içinde yatay kaydırılır

```
canlı     5 kolon  → sığar
arşiv     9 kolon  → sığmaz, .card .body{overflow-x:auto} ile kaydırılır
```

**Yükseklik kilidi kalktı.** `html,body{height:100%}` + `main{flex:1}` sayfayı
görünür alana kilitliyordu. Telefon tarayıcısının adres çubuğu kaydırınca
kaybolduğu için görünür yükseklik sürekli değişiyor; sonuç altta boş siyah alan
ve kaydırılamayan bir sayfa. Telefonda `height:auto` + `min-height:100dvh`;
tablo ve sohbet kendi içinde kaydırılır (`dvh` bilmeyen tarayıcı için
`min-height:100%` yedeği duruyor).

**Bir sütun hatası düzeltildi:** canlı tabloda başlık `P DRV TEAM GRID GAP` iken
hücreler `pos, drv, team, gap, grid` sırasındaydı — yani `GRID` başlığının
altında yarış farkı, `GAP` altında grid sırası görünüyordu. Artık ikisi de
`GRID → grid`, `GAP → gap`.

### Asistan: sabit alt panel (sheet)

Telefonda sohbet artık sayfanın en altında **sabit, katlanabilir** bir panel.
Canlı sayfada `#chat`, arşivde `aside#chatpanel`. Masaüstünde ikisi de eskisi
gibi normal akışta; bu tamamen `<=640px` kuralı.

```
kapalı : ┌──────────────────────────┐
         │ ▴ Asistan · son cevap…   │  46px şerit
         └──────────────────────────┘
açık   : ┌──────────────────────────┐
         │ mesajlar (kaydırır)      │  en fazla 78dvh
         │ [soru yaz…] [Sor]        │
         └──────────────────────────┘
```

**Neden balon (FAB) değil:** kapalı şerit son cevabın ilk satırını gösteriyor,
yani soruyu sorup paneli kapatabiliyorsun, tabloyu izliyorsun, cevap gelince
şeritte beliriyor. Balonda kapalıyken yalnız bir ikon olur — "cevap geldi mi"
bilgisi kaybolur. Ayrıca sağ alttaki 56px'lik daire, telefonda tam olarak
`GAP`/`INT` kolonlarının üstüne denk gelirdi.

**Klavye (gerçek bir Android telefonda):** panel klavyenin arkasında kalıyordu, iki
katmanlı düzeltme:

1. `<meta name="viewport" … interactive-widget=resizes-content>` — klavye
   açılınca layout viewport küçülür, `bottom:0` klavyenin üstüne oturur.
   Varsayılan (`overlays-content`) klavyeyi sayfanın üstüne bindirdiği için
   panel tam olarak arkasında kalıyordu.
2. `visualViewport` yedeği — anahtarı desteklemeyen tarayıcı için
   `bindKeyboard()` görünür alan farkını ölçüp paneli o kadar yukarı itiyor
   (`innerHeight - vv.height - vv.offsetTop`). 80px eşiği, adres çubuğu
   dalgalanmasının klavye sanılmasını önler.

Yazı alanı `font-size:16px`: iOS/Samsung odakta sayfayı yakınlaştırmasın.

**Yükseklik `height` ile AÇIKÇA verilir — `max-height` yetmez.** İki ayrı
hata buradan çıktı, ikisi de telefonda görüldü:

1. İlk sürüm kapalı hâli `translateY(calc(100% - 46px))` ile yapıyordu ve
   46px'ten **fazla** içerik görünüyordu. `%` transform öğenin kendi kutusuna
   göre hesaplanır, kutunun yüksekliği içeriğe göre değişir; aradaki fark
   ekranda kalıyordu.
2. Sonra `max-height:46px` yazıldı ama panel **hiç kapanmadı**. Sebep:
   `#chat{min-height:58dvh}` — sohbet akıştaki bir kolonken konmuş, panel
   sabitlenince kaldırılması unutulmuş bir kural. **CSS'te `min-height`,
   `max-height`'ı yener.** Kural silindi, yerine `height:78dvh` /
   `.collapsed{height:46px}` geldi.

`height` ayrıca `#msgs{flex:1}` için şart: flex çocuğu ancak kabın belirli bir
yüksekliği varsa esner. Sadece `max-height` olsaydı panel içeriği kadar
yüksek olur, mesaj alanı büyümez, boşluk boşa giderdi — "panel sayfaya
oturmuyor" görüntüsü buydu.

`tests/ui_test.js` bölüm 7 bunu metin üzerinden sınıyor: panelde sıfır olmayan
`min-height` varsa ya da `.collapsed` yalnız `max-height` kullanıyorsa test
kırılır. Tarayıcı olmadan bu tür bir öncelik hatası başka türlü görünmez.

**Kapalı hâlde `pointer-events:none`** — tutamak dışındaki her şey tıklanamaz,
yoksa klavye açıkken arkadaki forma dokunulabiliyordu.

**Açıkken tutamak sadece tutma çubuğu.** Etiket ve önizleme gizleniyor:
tutamak "ASİSTAN" yazarken hemen altında sekme "ASİSTAN" vardı, çift etiket.

**Çift başlık yok.** Tutamak "Asistan" / "Arşiv sohbeti" yazıyor; hemen altında
aynı başlığın tekrarı vardı. Telefonda `#chathead .t` ve arşivde
`.chathead .ttl` gizli, düğmeler (düşünme/temizle/tercih) duruyor.

**Tutamaç `flex:0 0 46px`.** Sadece `height:46px` yetmez: `#chat` bir flex
kolon ve kapalıyken toplam yüksekliği 46px; `flex-shrink:1` olan tutamaç
12px'e eziliyor, altındaki içerik onun yerine görünüyordu (ölçüldü: 12px).

**Panel %62, klavye açıkken %100.** %78'de tablo tamamen örtülüyordu
(ölçüldü 713/915px). Panelin işi tabloyu gizlemek değil yanında durmak.
`visualViewport` küçüldüğünde (klavye açıldı) `height` görünür alana eşitleniyor
— yazarken cevabı da görüyorsun.

**`chathead` tek satır.** `flex-wrap:wrap` ile üç öğe 412px'e sığmıyordu ve
`temizle` alt satıra düşüp başlığı 74px yapıyordu (ölçüldü). Artık `nowrap`;
`#pref` metni 108px'te üç noktayla kısalıyor.

**`[hidden] { display:none !important }`** — `static/theme.css`'te. Tarayıcı
varsayılanının özgüllüğü çok düşük (0,0,0); `.subtabs{display:flex}` onu ezip
`hidden` öğeyi görünür bırakıyordu. Bu yüzden telefonda sekmeler kaldırıldığı
hâlde çubuk ekranda kalıyordu.

### Geçici puan durumu telefonda panelin dışında

Canlı sayfada "Geçici puan durumu" panelin içinde bir alt sekmedeydi. Telefonda
bu iki şey demekti: 30px'lik sekme çubuğu doğrudan mesaj alanından çalıyor ve
sekme mantığı anlamsız — sohbet zaten sabit panelde, geçici tablo ise arada bir
bakılan bir özet.

`hoistProv()` (640px altında) `#pane-points` düğümünü panelden çıkarıp
`#provslot`'a taşıyor: Race Control'ün altında, sayfa akışında, her zaman açık.
Sekme çubuğu gizleniyor. Masaüstünde hiçbir şey değişmiyor (sekmeli hâli doğru).

**DOM taşınıyor, kopyalanmıyor:** `renderProvisional()` tek bir `#plist`'e
yazmaya devam ediyor, iki kopya tutma riski yok.

### İki sohbet paneli birebir aynı

Canlı ve arşiv sayfalarının sohbet bloğu **aynı sırada, aynı ölçülerde**:

```
head (başlık · düşünme · tercih · temizle)
msgs
chips          (öneri soruları)
usage          (token/kredi şeridi)
form           (textarea + Sor)
srcline        (Veri: tur N · …)
```

Sıra tesadüf değil. Arşivde `düşünme` formun **üstünde** ayrı bir `.lvl`
bloğuydu, canlıda `chathead` içinde; bu yüzden iki sayfada form farklı
yükseklikte başlıyordu. Karşılaştırma ölçümle yapıldı (1280px ve 412px):

| alan | hedef | ölçülen (ikisinde aynı) |
|---|---|---|
| sohbet kolonu | eşit genişlik | 453px @1280 · 412px @mobil |
| chips alt kenarı | aynı | 778 |
| usage | aynı | 20px @778 |
| form | aynı | 69px @798 |
| srcline | aynı | 33px @867 |

**Kolon oranı da eşitlendi.** Arşiv `1fr : 0.42fr` idi (1280px'te 379px),
canlı `1.55fr : 0.85fr` (453px). Dar kolonda aynı usage şeridi 2 satıra
düşüyor, 20px yerine 45px oluyor ve altındaki her şey 25px kayıyordu.
Yani "paralel olsun" isteğinin ön koşulu genişliği eşitlemekti.

**Öneri çipleri arşive de geldi.** Canlıdaki "Şu an en hızlı kim" gibi sorular
arşivde anlamsız; arşive uygun dört soru yazıldı ("Bu turda en hızlı kim",
"İkinci stint neden kötüydü"…). Sınıf adı `.schip`, çünkü `.chip` bu sayfada
**sürücü seçme pillerinde** (`#gapchips`) zaten kullanılıyor — aynı adı
vermek o pillerin stilini bozardı.

**Telefonda çipler kaldırıldı** (2026-10-31). Sabit alt panelde dört çip
iki satıra sarıyor, ~60px yiyordu; panelin %62'sinde sohbetin kendisi
neredeyse görünmüyordu (ekran görüntüsünde görüldü). `@media
(max-width:640px)` içinde canlıda `.chips`, arşivde `.schips` yalnız
`display:none` alıyor — HTML ve tıklama mantığı yerinde, masaüstünde
çipler duruyor (ölçüldü: 1280px'te `.chips` hâlâ `display:flex`).

### "kaynak live" yanlış okunuyordu

Arşiv sayfasının altında `Veri: tur 56 · kaynak live` yazıyordu. `snap.source`
frame'i hangi **yazıcının** ürettiğini söyler (`live` = canlı yakalayıcı,
`openf1` = topluluk aynası) — "veri şu an canlı" demek değil. Sayfa salt
okunur bir arşiv kaydı gösterdiği hâlde etiket "canlı" diyordu.

Artık ikisi de yazılıyor:

```
arşiv : Veri: tur 56 · arşiv kaydı (canlı yakalama)
canlı : Veri: tur 56 · canlı yayın · feed 20:30:44Z
```

Uydurma yok: eşleme iki gerçek değeri iki gerçek ada çeviriyor.

**Canlı sayfaya `#srcline` eklendi.** Aynı bilgi orada `#stamp` içindeydi ama
`#stamp` başlıkta ve `.head-info.optional` 640px altında gizli — yani mobilde
hangi veriyle konuştuğun hiç yazmıyordu. İki sayfada da sohbetin altında.

### Markdown tek yerde

Model cevapları hafif işaretli geliyor: `**kalın**` ve `` `kod` ``. Biçimlendirici
uzun süre **yalnız `index.html`'de** vardı; arşiv `esc()` kullanıyordu ve
`**BEA** (Bearman)` işaretleriyle birlikte görünüyordu. Canlı sayfa doğru
göründüğü için kimse arşive bakmamıştı — iki kopya tutmanın bedeli bu.

Artık `static/shell.js`'te iki fonksiyon var:

- `F1.fmt(t)` — `innerHTML` için, **önce kaçırır** sonra biçimlendirir.
  Modelin çıktısı güvenilmez metindir; sıra tersine dönerse XSS olur.
- `F1.plain(t)` — `textContent` ile yazılan yerler için (panel şeridi) aynı
  işaretleri siler.

**Başlıklar ve maddeler de burada.** Gerçek bir arşiv cevabı `## Bu yarış henüz
bitmedi` diye başladı ve panele çiğ hashlarla düştü: biçimlendirici yalnız
`**kalın**` ve `` `kod` `` biliyordu, model ise kendiliğinden Markdown başlığı ve
`- ` listesi yazıyor. `fmt()` artık satır başındaki `#{1,6}` ve `[-*]` işaretlerini
de çeviriyor; `^` + `m` bayrağı sayesinde "C#7" ya da "3 - 2" bozulmuyor.
`plain()` aynı işaretleri siliyor, çünkü kapalı panelin şeridi `textContent`
kullanıyor — iki renderer tek girdi.

## PWA — dört yüzey de kurulabilir uygulama

Dört sayfa da artık PWA: telefonda "Ana ekrana ekle" dendiğinde tarayıcıda bir
kısayol değil, **adres çubuğu olmayan kendi penceresinde** açılan bir uygulama
kurulur ve çevrimdışıyken de açılır.

Dört ayrı uygulama kurulur (canlı, arşiv, puan durumu, hafta sonu), tek uygulama
değil: dördü farklı zamanlarda farklı soruyu cevaplıyor. Tek ikon olsaydı, sezon
dışında puan durumu için kurulan kısayol her açılışta canlı timing tablosuna
düşerdi; salı günü açılan hafta sonu kısayolu da boş bir tabloya.

### Önbellek adı dosya içeriğinden türer (elle bump YOK)

`CACHE_VERSION` sabitti (`"f1-shell-v1"`) ve yanında "önbelleğe giren bir dosya
değişince bump et" yorumu vardı. Kimse bump etmedi: sayfa, `theme.css` ve
`shell.js` değişti ama **worker'ın kendi baytları değişmedi**, tarayıcı yeni
worker'ı hiç kurmadı ve dönen kullanıcıya eski sayfa + eski JS servis edildi.
Kullanıcının raporu tam buydu: *"değişiklik yok gibi"*.

Artık ad **önbelleğe giren baytların hash'i** (`f1-shell-<12 hex>`), yüzeyin kendi
sayfası dahil. Herhangi bir düzenleme adı değiştirir → `/sw.js` değişir → tarayıcı
yeni worker'ı kurar → `activate` eski önbelleği siler. Hatırlanacak adım yok.
Dört yüzey ayrı ad alır, yoksa iki kurulu uygulama aynı önbelleği paylaşıp
birbirini silerdi. `tests/test_pwa.py` adın türetildiğini, yüzeye göre
farklılaştığını ve değişmeyen dosyalarda kararlı kaldığını sınıyor.

**Ders:** "yayınlarken şunu yap" diyen bir yorum, yapılmayan bir adımdır.

### Kablolama tek yerde: `pwa.py`

`server.py`, `archive.py`, `standings_app.py` ve `welcome_app.py` aynı `pwa.install(app, yüzey)`
çağrısını yapıyor. Her yüzey kendi `/manifest.webmanifest` ve `/sw.js` yanıtını
üretiyor; ikisi de **kök** yolda, `/static` altında değil — bir service worker'ın
kapsamı kendi dizinidir, `/static/sw.js` olsaydı yalnız `/static/`'i kontrol eder
ve hiçbir sayfa açılışını görmezdi.

| dosya | ne verir |
|---|---|
| `/manifest.webmanifest` | yüzeye özel ad/kısa ad, `display: standalone`, 192+512+maskable ikon |
| `/sw.js` | `static/sw.js` şablonu; önbellek adı ve precache listesi Python'dan doldurulur |
| `/favicon.ico` | 32px ikon (tarayıcı kökü ayrıca ister) |
| `/static/icons/*` | ikon seti, mevcut `/static` mount'undan |

### `start_url` neden `"./"`

Her yüzeye kendi portundan, kök yoldan ulaşılıyor (`http://127.0.0.1:8099/`), yani
`start_url: "/"` bugün çalışırdı. Yine de `"./"`: `"/"` mutlaktır ve uygulama bir
gün herhangi bir önek altında sunulduğu an bozulur; doğru olmanın maliyeti yok.
Manifest standardına göre `start_url` ve ikon `src` yolları **manifestin kendi
adresine göre** çözülür; `"./"` demek "bu manifest hangi dizinden geldiyse orası"
demek. Böylece yüzeylerin kendi adresini bilmesi gerekmiyor.

### Önbellek stratejisi (`static/sw.js`)

| istek | davranış | neden |
|---|---|---|
| sayfa açılışı (`navigate`) | önce ağ, olmazsa önbellek | kabuk anında açılsın ama yeni sürümü gizlemesin |
| `GET /api/...` | önce ağ, olmazsa önbellek | bayat anlık görüntü **dürüst**: her yüzey kendi damgasını yazıyor, `shell.js` çevrimdışı rozetini gösteriyor |
| diğer GET (css/js/ikon/manifest) | stale-while-revalidate | kurulu uygulamanın "anında açılır" hissi bunlardan geliyor |
| `POST`/`DELETE` | yalnız ağ | sohbet/ayar/yenileme bir yazma; sunucunun görmediği "başarılı" hatadan kötü |
| `/api/stream` (SSE) | hiç dokunulmaz | hiç bitmeyen bir yanıtı önbelleğe yazmak Cache API'yi kilitler |

Precache listesi ya hep ya hiç: `cache.addAll` tek bir 404'te reddeder ve yarım
kalmış önbellek en kötü durumdur (var görünür, ihtiyaç duyulan dosyada patlar).
Bu yüzden kurulum başarısız olursa worker hiç kurulmaz — DevTools'ta görünür.

`CACHE_VERSION` (`pwa.py`) tek harfli bir sürüm damgası: `activate` içinde adı
tutmayan her önbellek siliniyor, yani sürümü artırmak tahliyeyi yapıyor.

### Canlı sayfada `/api/state` yedeği

Canlı yüzey verisini yalnız `EventSource` (`/api/stream`) üzerinden alıyordu.
Akış kurulamazsa — çevrimdışı, ya da SSE'yi kesen bir vekil — ekranda sonsuza
kadar "veri bekleniyor..." kalıyordu, oysa worker'ın önbelleğinde taze bir kare
vardı. Artık `es.onerror` ve sayfa açılışı `/api/state`'i de çağırıyor (o uç
`GET /api/...` olarak önbelleğe alınıyor). Kare **eski** olduğu için sayfa kendi
damgasını ve çevrimdışı rozetini gösteriyor; uydurulmuş bir "şimdi" yok.

Akış ile yedek çağrı yarıştığı için guard `updated` alanında: ISO ve saat dilimi
damgalı. `server_time` gün içi saat ("20:43:47") ve gece yarısını geçen oturumda
dize karşılaştırması yanlış kareyi seçerdi.

### Çevrimdışı rozeti

`shell.js` başlığa `.badge.warn` sınıfında bir `#netbadge` ("çevrimdışı") koyuyor
ve `online`/`offline` olaylarında açıp kapatıyor. Yeni CSS yok. Rozet gerekli
çünkü worker kabuğu **ve** son API anlık görüntüsünü önbellekten veriyor: rozetsiz
bir çevrimdışı sayfa, dünkü sayılarla canlı bir sayfadan ayırt edilemezdi.

### HTTPS şartı

`navigator.serviceWorker.register()` yalnız **secure context**'te çalışır:

| erişim | PWA | sebep |
|---|---|---|
| `http://127.0.0.1:8099` | ✅ | localhost güvenli sayılır |
| `http://192.0.2.10:8099` | ❌ (normal sayfa) | düz HTTP, secure context değil |
| `https://senin-adresin/...` | ✅ | TLS, nasıl sonlandırırsan |

`shell.js` bunu `isSecureContext` ile kontrol edip kaydı sessizce atlıyor: düz
HTTP'lik bir LAN adresinden açılan sayfada her yüklemede konsol hatası görmenin
anlamı yok. Sayfanın kendisi çalışmaya devam eder; yalnız kurulum yapılamaz.

### İkonlar

`design/pwa-icon.svg` tek kaynak; `tools/make_icons.py` beş PNG üretir
(192, 512, maskable-512, apple-touch-180, favicon-32). Bu makinede ImageMagick ve
librsvg yok, snap Chromium da `snap-confine` yüzünden çalışmıyor; rasterleştirme
testlerin zaten kullandığı `chrome-headless-shell` ile yapılıyor.

Maskable varyant ayrı üretiliyor: Android maskable ikonu karenin ortadaki %80'ine
denk gelen daireye kırpıyor, normal sanat eseri maskable diye bildirilseydi
göstergenin iki ucu telefon ana ekranında yenirdi.

### Sınanan şey

- `tests/test_pwa.py` — sunucusuz: precache'teki her dosya var mı, `start_url`
  göreli mi, install kriterleri (192+512+maskable, standalone) tam mı, doldurulmuş
  `sw.js` geçerli JS mi, SSE ve yazmalar atlanıyor mu, dört sayfanın `<head>`'i
  manifesti bağlıyor mu, `theme_color` ile `--bg` ayrışmamış mı, her manifestin
  arkasında gerçekten servis edilen bir sayfa var mı.
- `tests/pwa_test.js` — gerçek tarayıcıda: worker kaydoluyor ve kapsamı sayfayı
  kapsıyor, kabuk+ikonlar önbellekte, **şebeke kesilip** sayfa yenilendiğinde
  kabuk önbellekten geliyor ve rozet görünüyor, arşiv ve hafta sonu yüzeyleri
  kendi worker + manifestlerini alıyor.

## Yalnız yerelde çalıştırma

Bu sürüm **tasarım gereği yalnız loopback'te** çalışır. Repoda tünel, mesh VPN,
ters vekil entegrasyonu ya da yol öneki yönlendiricisi **yoktur**: dört yüzey
`127.0.0.1`'e bağlanır ve kendi portlarından açılır.

| Yüzey | Adres |
|---|---|
| Hafta sonu | http://127.0.0.1:8103/ |
| Canlı | http://127.0.0.1:8099/ |
| Arşiv | http://127.0.0.1:8100/ |
| Puan Durumu | http://127.0.0.1:8101/ |

Bu bilinçli bir tercih ve kapsam kararı olduğu kadar **güvenlik kararıdır**:
**uygulamada kimlik doğrulaması yoktur.** Sunduğu her istek, arşivi görmeye, AI
sağlayıcısını değiştirmeye ve API kredisi harcamaya yetkili biri tarafından
gelmiş sayılır. Bunu güvenli kılan şey loopback'e bağlanmasıdır. `F1_HOST=0.0.0.0`
ile bağlanma adresini genişletirsen, bu yetkileri portuna erişebilen herkese
vermiş olursun.

### Başka bir makineden erişmek istersen

Erişebilirsin ve bu senin kararın — ama neyi açtığını bilerek yap ve önüne
kendi kimlik doğrulamanı koy. Uygulamayı değiştirmeden iki yol var:

- **Kimlik doğrulamalı ters vekil** (Caddy, nginx, Traefik): her portun önüne,
  TLS sonlandırarak. Doğrulama katmanı olmadan port açma.
- **Özel ağ** (Tailscale, WireGuard, SSH tüneli): port yalnız kendi cihazlarından
  erişilebilir olsun.

İkisi de burada yapılandırılmış değildir. Repodaki hiçbir şey bunlardan birini
varsaymaz ve hiçbir kod yolu vekil arkasında olmaya bağımlı değildir — her URL
sunulduğu kökene göre görecelidir, yani yolları bozmayan bir vekil değişiklik
gerektirmeden çalışır.

### Uygulama neden hâlâ kalp atışı gönderiyor?

`/api/stream` ve `/api/chat/stream`, söyleyecek sözü yokken birkaç saniyede bir
yorum satırı yazar. Loopback'te bu neredeyse anlamsızdır — ama birkaç bayt
maliyeti vardır ve birinin önüne bayt tamponlayan herhangi bir aracı koyduğu gün
"çalışıyor" ile "asılı kalıyor" arasındaki farktır. Mekanizma korunuyor; "her
zaman bir aracı vardır" varsayımı korunmuyor.


## Geçmiş yarışı arşive ekleme

F1'in kendi statik arşivi artık auth duvarının arkasında (her `*.jsonStream` isteği 401),
yani canlı yakalanmayan bir yarış resmî feed açısından kayıp. OpenF1 topluluk aynası
bağımsız bir kopya tutuyor ve bir tur çerçevesini yeniden kurmaya yetecek kadar zengin.

```bash
./run.sh backfill --list                        # arşivde ne var
./run.sh backfill --race-id 1 --phase laps      # güvenli: sadece tur süreleri
./run.sh backfill --race-id 1 --phase sectors   # sektörler + mini sektör kodları
./run.sh backfill --race-id 1 --phase speeds    # i1/i2/st hızları
./run.sh backfill --race-id 1 --phase frames    # turları yeniden kur (daha büyük iş)
./run.sh backfill --all --phase both --dry-run  # hiç yazmadan dene

./run.sh backfill --ingest-season 2026 --phase both --dry-run  # plan: ne eklenecek
./run.sh backfill --ingest-season 2026 --phase both            # sezonu indir + kur
```

`--race-id`/`--all` **var olan** yarış kayıtlarını doldurur; hiç canlı yakalanmamış bir
yarışın `races` satırı olmadığı için `--ingest-season` eklendi. O mod OpenF1'den sezonun
Race seanslarını okur, `is_cancelled` olanları ve `date_start`'ı geçmemiş yarışları atlar,
arşivde olmayanlar için canlı yazarla aynı biçimde kayıt açar
(`2026/2026-03-08_Australian_Grand_Prix/2026-03-08_Race/`), adı Jolpica takviminden alır
(OpenF1 yeri bilir, yarış adını bilmez; takvim yoksa `<ülke> Grand Prix` yazar ve bunu
söyler), yarış mesafesini **süresi olan son turdan** alır, sonra normal fazları çalıştırır.
Kaydı backfill açtıysa `races.source='openf1'` olur — canlı yakalamaymış gibi görünmez.

Bir uyarı: ayna her yarışta tam değil. 2026 Avustralya'da OpenF1 sürücü başına yalnızca
birkaç tur döndürüyor (20 tur süresi, 8 çerçeve); o yarış arşivde ince kalır ve başka bir
kaynaktan uydurulmaz. Belçika'da da 45 numaralı tur kaydı süresizdir, bu yüzden yarış
mesafesi 44 tur sayılır.

Eşleştirme **`meeting_key`** ile yapılır — F1'in `SessionInfo.Meeting.Key` değeri OpenF1'in
`meeting_key` değeriyle aynı numaralandırmadır, yani tarih/tur tahminine gerek yok.

### Provenance (kaynak etiketi)

Her tur satırı iki ek alan taşır:

- `frames.source` — `live` (canlı feed'den kaydedildi) veya `openf1` (arşivden kuruldu)
- `frames.missing` — bu kaynağın sağlayamadığı alanların listesi (örn. `["grid"]`)

Bu süs değil. OpenF1 canlının üst kümesi **değil**: canlıda sektör segmentleri, tur içi
~0.27 sn çözünürlük ve tam race-control akışı var; OpenF1'de eksik olabilen ama
kaçırdığımız turlar/farklar var. `missing` listesi olmadan arşiv sohbeti olmayan sektör
verisini uydurur — "uydurma yasak" kuralı tam burada kırılır. Bu yüzden:

- `analysis.context_text()` brifinge `KAYNAK:` ve `EKSİK ALAN KURALI:` satırlarını ekler,
- arşiv arayüzü başlıkta "eksik alan: …" rozetini gösterir, tabloda `—` basar,
- `--overwrite` verilmedikçe backfill canlı satırın üzerine **asla** yazmaz
  (`INSERT OR IGNORE`): canlı veri daha zengin, o kazanır.

Başlangıç gridi OpenF1'de yok ama yarış boyunca sabit; aynı yarışın canlı çerçevesinde
varsa devralınır (`store.all_row_grids`), yoksa `missing`'e yazılır.

## Puan durumu: resmî tablo ve geçici tablo

İki ayrı soru, iki ayrı tablo, **tek puan aritmetiği** (`points.py`):

| | Nerede | Ne gösterir | Kaynağı |
|---|---|---|---|
| **Resmî sıralama** | Puan Durumu sayfası (:8101) | Sezonun kesinleşmiş sürücü/marka puanları | Jolpica API → SQLite önbelleği |
| **Geçici tablo** | Canlı sayfa (:8099), "Geçici puan durumu" sekmesi | Yarış *şu anki sırayla bitse* tablo ne olurdu | Taban (önbellek) + canlı sıra |

### Puan tablosu tek yerde

`points.py` içinde `RACE_POINTS = (25,18,15,12,10,8,6,4,2,1)` ve
`SPRINT_POINTS = (8,7,6,5,4,3,2,1)`. Başka hiçbir modül bu sayıları tekrar etmez;
canlı panel, puan durumu sayfası ve testler aynı fonksiyonu çağırır. İkinci bir kopya
"üslup sorunu" değildir — kopyalar ayrışır ve aynı ekrandaki iki yüzey aynı sayı için
farklı şey söyler.

Bilinçli olarak **yapılmayanlar**:

- **En hızlı tur puanı yok.** 2024'e kadar vardı, 2025'te kaldırıldı. Tabloda tutmak,
  artık var olmayan bir puanı uydurmak olurdu. Geri gelirse tablo girdisi olarak gelir,
  kod içine gömülü bonus olarak değil.
- **Yarım puan yok.** Canlı feed, yarışın kısalacağını önceden söyleyemez; yarıya
  bölünmüş tablo bir tahmin olurdu. Arayüz bunun yerine "tam puan varsayımı" der.
- **Tie-break yok.** Eşit puanda resmî sıra, sezonun tüm sonuçları üzerinden en iyi
  derece sayımıdır — elimizde olmayan veri. Bu yüzden eşit toplamlar `tie` olarak
  işaretlenir ve **aynı sırayı paylaşır**; ekrandaki sıra resmî sıra gibi sunulmaz.

### Çevrimdışı çalışma: önbellek + bayatlık

Puan durumu sayfasının varlık sebebi ağ olmadan da açılması. Bu yüzden okuma ve yazma
yolu ayrıdır:

- **Okuma** (`standings.get_standings_view`) ağa hiç dokunmaz; SQLite'tan okur.
  Yanında `fetched_at`, `age_seconds`, `stale` ve varsa son başarısız denemenin hatası
  döner.
- **Yazma** (`standings.refresh`) API'den çeker, normalize eder, önbelleğe yazar.
  Başarısız deneme de `standings_attempts` tablosuna yazılır — sessiz kalmaz.

Sayfa üç durumu **ayrı ayrı** söyler:

1. **Taze:** tablo normal görünür, şeritte kaynak + son güncelleme yaşı yazar.
2. **Bayat:** sarı uyarı — "son çekilen anlık görüntü gösteriliyor (tarih, yaş önce).
   Aradan geçen yarışlar burada eksik." Eksik sıra **sıfırla doldurulmaz**.
3. **Hiç veri yok:** kırmızı uyarı — "bu sezon için kayıtlı sıralama yok, puan tablosu
   uydurulmuyor", varsa son hatanın metni.

Önbellek tazeliği 6 saat (`standings.FRESH_FOR`); başarısız çekimden sonra 2 dakika
boyunca yeniden denenmez (`RETRY_AFTER`) — ölü bir ağda her sayfa açılışının DNS
zaman aşımı beklemesi, sayfayı kullanılamaz yapardı.

Kaynak adresi `F1_STANDINGS_API` ile değiştirilebilir (ayna/DNS değişimi veya ağsız
test için). Varsayılan: `https://api.jolpi.ca/ergast/f1`.

### Geçici tablo: "yarış sonu sırası bu olursa"

Canlı sayfadaki sekme, `points.provisional()` çıktısını çizer:

```
taban puanlar (bu yarış hariç)  +  sürücünün MEVCUT sırasının puanı  =  geçici toplam
```

Her satırda taban puan (soluk), bu yarışın katkısı (`+25` yeşil, puan almayan `—`) ve
geçici toplam var. Sıra değişince tablo bir sonraki SSE karesinde kendiliğinden
güncellenir — ayrı bir çekim yok, ek maliyet yok. Taban sırasına göre kaç sıra
kazanıldığı/kaybedildiği `▲2` / `▼1` ile görünür; **değişmeyen satırda işaret yoktur**
(tire basmak "hiçbir şey kaybetmedi" gibi okunurdu).

**Çift sayma koruması.** Projeksiyonun yalan söyleyebileceği tek yer burası: önbellekteki
taban, gösterilen yarışı zaten içeriyorsa puanlar iki kez sayılır. `standings.
base_rows_for_projection()` bunu iki yoldan denetler ve şüphelenirse **projeksiyonu
reddeder**:

- takvimden bulunan tur numarası: `taban_round >= yarış_round` ise reddet;
- takvim yoksa tarih: `fetched_at >= yarış tarihi` ise reddet.

Reddedildiğinde panel tablo çizmez, gerekçeyi yazar. Seans bittiyse mesaj değişir:
"seans bitti — resmî sıralama artık kesinleşti, güncel tablo Puan Durumu sayfasında".

Taban tabloda **olmayan** bir sürücü (ör. sezon ortası değişiklik, önbellek eski) puan
alır ama tabloya eklenmez; adı ayrıca "bu yarışta sırada ama taban tabloda yok" olarak
listelenir. Kimin satırı olduğu uydurulmaz.

### Takvim

`standings_snapshots` tablosu `kind='schedule'` ile sezon takvimini de tutar. Amacı tek:
"şu an gösterilen yarış kaçıncı tur" sorusunu **tahmin etmeden** cevaplamak. Eşleştirme
takvim tarihiyle yapılır; aynı güne iki yarış düşerse isimle daraltılır, o da olmazsa
`None` döner (tahmin yok).

## Maliyet

Ölçüldü: RSS ~62 MB, canlı yakalama bir çekirdeğin ~%0.02'si. Tur çerçevesi ~12.9 KB →
yarış başına ~0.8 MB; sektör satırı 132 B → yarış başına ~0.18 MB. Sezon başına toplam
~23 MB. Sohbet mesajı ~1 KB. Backfill kısa transaction'lar hâlinde yazar (12 çerçeve /
500 tur süresi) ve `busy_timeout=15000` ile bekler, böylece yarış sırasında canlı
yakalamayı asla kilitlemez.

Sektör istatistiği `snapshot()` içinde her saniye hesaplanır, bu yüzden en iyi-sektör
taraması 5 saniyelik bir önbellekle korunur: soğuk 15.4 ms, sıcak 0.7 µs. `/api/state`
yanıtı 4-24 ms arasında kalıyor.

## Sektör ve mini sektör

Sektör süreleri (`S1/S2/S3`) gerçek veridir ve üç yerde bulunur: canlı feed
(`TimingData.Sectors`), OpenF1 (`duration_sector_1..3`, turların %99'unda dolu) ve
dolayısıyla veritabanındaki `sectors` tablosunda.

**Mini sektör süreleri hiçbir kaynakta yok.** Ne canlı feed ne OpenF1 bir mini sektörün
*süresini* yayınlar; yalnızca her birinin durum kodunu verir (`2048` kişisel en iyi,
`2049` seans en iyisi, `2051` gelişme yok). Bu kodlar `seg1..seg3` kolonlarında CSV
olarak saklanır — anlamlarını uydurmadan, kanıt olarak. "Mini sektör 2'de 80 ms kaybetti"
cümlesi hiçbir veriyle kurulamaz; bu yüzden arşivde böyle bir cevap verilmez.

Sektörler `frames.snap` içine gömülmez, ayrı tabloda durur. Gömülseydi tur çerçevesi
12.9 KB'den 31.4 KB'ye çıkardı (sektörler çerçevenin %56'sı) — yani sezon başına 23 MB
yerine 42 MB, üstelik sorgulanamayan JSON olarak. Ayrı tabloda her tur üç sayı ve bir
arama indeksi olur.

İki türetilmiş sayı sektörü cevaplanabilir kılar:

- **İdeal** — sürücünün kendi en iyi sektörlerinin toplamı; hepsini tekrarlayabilseydi
  yapacağı tur.
- **Kayıp** — gerçek tur süresi eksi ideal; harcanan zaman.

İkisi de iki yüzeyde aynı `analysis._sector_stats` fonksiyonundan gelir, böylece canlı
ajan ile arşiv aynı sayıyı görür. Arşiv tablosunda yarışın en hızlı sektörü mor
işaretlenir; yayınlanmamış bir sektör `—` kalır, sıfır ya da tahmin basılmaz.

## Brifing kapsamı: saklanan her alan yazılı mı

Bugüne kadarki en pahalı hata sınıfı **sessiz boşluk** oldu: veri veritabanında
vardı, brifinge girmiyordu, ajan da dürüstçe "bu veri feed'de yok" diyordu.
Sektörler, tur bazlı sıra, takvim tarihi, tam isimler, RETIRED bayrağı ve pit lane
turları bu şekilde, her biri ayrı bir kullanıcı sorusuyla keşfedildi.

Sebep yapısal: ajan yalnız `context_text()`'in ürettiği **metni** görür, `frames.snap`
şemasını görmez. Elinde olmayanın envanterini çıkaramaz — negatif uzayı göremez.
Üstelik hata sessizdi: uydurma tutarsızlık bırakır, eksik öncülden doğan cevap ise
kusursuz tutarlı gelir.

`tools/audit_coverage.py` bunu kullanıcıya bırakmaz:

```bash
.venv/bin/python tools/audit_coverage.py --strict   # eksik varsa exit 1
```

Denetim **iki yönlü**:

1. **Saklanan → brifing.** `frames.snap`, satır alanları, `races` tablosu ve yardımcı
   tablolar (`sectors`, `lap_speeds`, `lap_times`) okunur; her alan üç kutuya ayrılır:
   **temsil ediliyor** / **bilinçli dışta** (gerekçesiyle) / **karar verilmemiş**.
2. **Kaynak → saklanan.** OpenF1 `/laps` cevabındaki her alan ya veritabanında olmalı ya
   da gerekçesiyle dışta bırakılmalı.

İkinci yön, birincinin kör noktasıydı: `i1_speed`/`i2_speed`/`st_speed` her cevapta
vardı ama hiç saklanmıyordu — "saklanan ↔ brifing" denetimi bunu göremezdi, çünkü alan
hiçbir zaman "saklanan" tarafında değildi. Bu boşluk ancak kullanıcı "mini sektör
OpenF1'de var demiştin" diye sorunca ortaya çıktı.

Her iki yön de doğrulandı: şemaya uydurma alan eklenince ve `OPENF1_LAPS`'ten bir alan
silinince betik yakalayıp exit 1 veriyor. Cevrimdisi için `--no-network`.

| Alan | Neden dışta |
|---|---|
| `color` | yalnız arayüz rengi |
| `num` | RC mesajlarında zaten `CAR 12 (ANT)` olarak geçiyor |
| `updated` | feed'de boş; zaman bilgisi `server_time` ve RC damgalarında |
| `stints` | TÜM TURLAR bloğu aralıkları (`[SOFT 1-15]`) zaten yazıyor |
| `is_pit_out_lap` | PIT kolonu + PIT LANE TURLARI satırı aynı bilgiyi veriyor |
| `date_start` | tur başlangıç zamanı; rapor içi simülasyon yok |
| `meeting_key`, `path`, `id` | eşleştirme/kimlik, iç kullanım |

Bu turda eklenenler (brifing 19.5k → 20.7k karakter, +%6):

| Blok | Ne sorulabiliyor | Önce |
|---|---|---|
| `YARIŞ KİMLİĞİ` | "bu yarış ne zamandı" | ❌ "veride tarih yok" (oysa `races.start_date` dolu) |
| `SÜRÜCÜLER` | "Hamilton nerede" — kod↔isim eşlemesi | ⚠️ modelin dünya bilgisine kalıyordu |
| `YARIŞ DIŞI` | "neden yarış dışı kaldı" | ❌ "COL yarış dışı kalmadı" — kendisiyle çelişti |
| `PİT LANE TURLARI` | "hangi turda pit yaptı" | ⚠️ stint aralığından çıkarım gerekiyordu |

`YARIŞ DIŞI` satırı bilinçli olarak "sebep bu bayrakta yok" der: RETIRED yalnız çekildiğini
gösterir, sebebini değil. Modelin uydurmaması için sınırı metinde çiziyoruz.

## Mini sektör: kod var, süre yok

Mini sektör **durum kodları** her iki kaynakta da var ve saklanıyor
(`sectors.seg1..seg3`, CSV):

| kod | anlam |
|---|---|
| `2048` | kişisel en iyi (P) |
| `2049` | seans en iyisi (S) |
| `2051` | gelişme yok (.) |

Mini sektör **süresi** hiçbir kaynakta yok — ne canlı feed'de ne OpenF1'de. Bu yüzden
`EN İYİ TUR` bloğunda bayrak dizisi olarak yazılır (`?P.SP./.SPPPPP./P.SSSPPS`), süre
gibi toplanmaz, "şurada 80 ms kaybetti" cümlesi kurulmaz. Bayrak dizisi "kim nerede
mordu" sorusunu cevaplar; "kaç ms" sorusunu sektör süreleri cevaplar.

Boş alan `?` olarak yer tutar, atlanmaz — atlansa iki sürücünün ızgarası kayar ve
pozisyonlar sessizce yanlış hizalanır.

### Mini sektör şeritleri (tabloda)

Canlı tabloda ve arşivde her sektör süresinin **altında** ince bir şerit duruyor: o
sektörün mini sektörleri. Şerit bir **süre değil durum** gösterir, bu yüzden bütün
çubuklar eşit genişliktedir — uzunluk hiçbir şey ifade etmez:

| renk | kod | anlam |
|---|---|---|
| mor | `2049` | seans en iyisi |
| yeşil | `2048` | kişisel en iyi |
| sarı | `2051` | gelişme yok |
| şeffaf | `0` | veri yok (feed kuyruğu sıfırla dolduruyor); yeri korunur, boyanmaz |
| soluk gri | diğer | eşlemede olmayan kod (`2064`, `2050`, `2052` arşivde görüldü); anlam uydurulmaz |

Sektör rengiyle **aynı dili** konuşur (mor = en hızlı) ama aynı ölçüm değil: sektör
rengi *süreleri* karşılaştırır, şerit *mini sektör kodlarını* gösterir. Kod hiç yoksa
şerit çizilmez — boş gri bir şerit "hepsi gelişme yok" gibi okunurdu.

Şerit, yanındaki sektör süreleriyle **aynı turdan** gelir: `row.segs` ekrandaki
turunkü, `row.b_segs` en iyi turunki (`analysis._sector_stats`). İki farklı turu tek
hücrede eşleştirmek sessiz bir yalan olurdu, bu yüzden ayrı alanlar.

## Hız noktaları (`i1`, `i2`, `st`)

OpenF1 her turda üç gerçek hız ölçümü veriyor: `i1_speed` (S1 sonu), `i2_speed`
(S2 sonu), `st_speed` (hız tuzağı). Bunlar **her `/laps` cevabında vardı ve hiç
saklanmıyordu** — sektör karşılaştırması yalnız saniye cinsinden yapılabiliyordu.

```bash
./run.sh backfill --race-id 1 --phase speeds   # 1164 satir, 1020'si uc hizi da dolu
```

`lap_speeds` tablosunda dururlar (`num, lap, i1, i2, st, source`), `put_speeds`
COALESCE ile birleştirir: kısmi bir yazma önceki değeri silmez.

`EN İYİ TUR` bloğu sürücü başına en hızlı turu, o turun **kendi** sektör sürelerini,
üç hızı ve mini sektör bayraklarını verir:

```
LEC | tur 42 97.976sn S=26.289/38.906/32.781 hiz i1/i2/st=215/171/310 mini=?P.SP./.SPPPPP./P.SSSPPS
HAM | tur 48 98.457sn S=26.076/39.274/33.107 hiz i1/i2/st=213/166/310 mini=?PPPPP/PPPSPPPP/PSPP.SPP
```

`S=` alanı şart: POS tablosundaki sektörler **güncel** turun sektörleridir. Bu alan
eklenmeden önce model en iyi tur süresini başka turun sektörleriyle eşleştirdi
(LEC'in tur 42 S1'ini 26.164 diye verdi — oysa 26.289; 26.164 tur 40'ındı). Blok
başlığı ve SYSTEM kuralı bu karışmayı açıkça yasaklar.

## Tur bazlı sıralama

Her tur çerçevesi `POS` kolonunu saklar, yani "LEC tur 15'te kaçıncıydı" sorusunun cevabı
veritabanında **vardı**. Brifinge girmediği için ajan "bu veri feed'de yok" diyordu — bu
eksik veri değil, yanlış cevaptı. Artık `TUR BAZLI SIRALAMA` bloğu her brifingde.

Blok, tur başına bir sayı yerine **aralık** yazar:

```
LEC | 1-13:3 14-15:2 16:6 17-19:5 20-24:4 25-39:2 40-46:4 47-48:3 49-56:2
```

Üç kodlama ölçüldü (bu yarış, 22 sürücü):

| kodlama | boyut | sorun |
|---|---|---|
| tam seri (`3 3 3 3 …`) | 3149 karakter | okunur ama brifingin +%18'i |
| değişim noktası (`14:2 16:6 …`) | 1852 karakter | **çıkarım gerektirir** |
| **aralık** (`14-15:2 16:6 …`) | **2116 karakter** | çıkarım yok |

Değişim noktaları en küçüğüydü ama testte model "tur 30'da kaçıncı" sorusunda önce "4."
deyip kendini düzeltti (doğru cevap P2). Aralık kodlaması 264 karakter daha pahalı ve
çıkarımı tamamen kaldırıyor: tur 30 → `25-39:2` doğrudan okunur. Brifing 17.1k → 19.5k
karakter (+%14).

Üç soru ham `frames` tablosuna karşı doğrulandı:

| soru | cevap | ham veri |
|---|---|---|
| "LEC stint 1'i kaçıncı bitirdi" | P2 | tur 15 → pos 2 ✓ |
| "LEC tur 30'da kaçıncıydı" | P2 | tur 30 → pos 2 ✓ |
| "VER ne zaman 3.'lüğe düştü" | 49. tur (47-48 P2) | tur 47/48 → 2, tur 49 → 3 ✓ |

Blok 5 saniyelik önbellekle korunur: kurulum 110 ms (56 çerçeve ayrıştırma), sıcak
çağrı bedava.

## Hafta sonu yüzeyi ve hafta sonu özeti

Dördüncü yüzey (`welcome_app.py`, 8103) bir yarışı değil **hangi seansların var
olduğunu** gösterir: hafta sonu seçilir, o hafta sonunun seansları kart olarak
çizilir. Kart durumu dört değerden biridir ve hepsi **yakalama kanıtından** gelir,
saatten değil:

| Durum | Ne demek |
|---|---|
| `live` | `last_seen` son 90 sn içinde ve seans bitmemiş (`server.touch_race` her 15 sn) |
| `done` | çerçeve/tur kaydı var, seans bitmiş ya da bayat |
| `scheduled` | hiç kayıt yok **ve** `start_date` gelecekte |
| `empty` | hiç kayıt yok, `start_date` geçmişte (yakalama kaçmış) |
| `missing` | hafta sonunda o seansın satırı hiç yok |

`scheduled` ile `empty` ayrımı önemli: ikisi de "veri yok" der ama biri "henüz",
diğeri "kaçırdık" demektir. Ve **gelecekteki `start_date` tek başına yeterli
değil**: gerçek arşivde Mexico City yarışının `start_date`'i bir gün ilerideyken
üç çerçevesi vardı. Bu yüzden önce kayıt kanıtına bakılır, saat yalnızca hiç kayıt
yokken konuşur (`weekend.session_state`).

### Kart etiketi neden kısa

Kart ~168px. Uzun etiket ("Antrenman 1") gerçek telefonda iki satıra kırılıyor,
rozet de yanına sıkışıyordu; mutlak konumlu rozet + sabit `padding-right` çözümü
ikinci kez kırıldı ("ANTRENM…KAYIT YOK" — rozet etiketin üstüne bindi). Doğru
çözüm piksel bütçesi değil **içeriğe göre boyutlanan sütun**: kart başlığı
`grid-template-columns: minmax(0,1fr) auto`, etiket sarmalanır, asla kırpılmaz.
Etiketler `store.KIND_SHORT` (`Ant. 1`), uzun karşılığı `store.KIND_LABELS`
brifingde kalır; ikisi yan yana durur ki sürüklenmesinler.

### Kart ızgarası ile brifing aynı hesabı kullanır

`weekend.py` iki çıktı üretir ama **tek hesaptan**: `overview()` kart ızgarasını,
`summary_block()` brifing matrisini. İkisi ayrı yazılsaydı aynı hafta sonu için iki
farklı sayı üretirlerdi — bu projenin tekrar tekrar düştüğü hata. Özet hafta sonu
anahtarı + (çerçeve, tur, durum) parmak iziyle memoize edilir; canlı seans
büyüdükçe tazelenir.

Özette sürücü × seans matrisi var; hücre içeriği seans tipine göre değişir
(antrenman: en hızlı tur / temiz uzun stint medyanı (tur sayısı); sıralama: en hızlı
tur + grid; yarış/sprint: bitiş sırası / en hızlı tur). Temiz tur = sürücünün en iyi
turuna göre %7 bandı içinde kalan turlar; 3'ten az temiz tur varsa medyan
**verilmez** — tek turdan uzun stint temposu üretmek uydurma olurdu.

### Sprint haftası nereden biliniyor

Ergast uyumlu takvimde sprint bayrağı yok. Üç kademe:

1. Hafta sonunda `SQ`/`SP` kaydı varsa → sprint (kanıt).
2. `sprint_rounds` tablosunda o sezon/tur varsa → sprint (küratörlü, elle girilir).
3. Hiçbiri yoksa → **normal hafta sonu varsayımı**, ve yüzey bunu
   `shape_source=default` olarak söyler; uydurulmuş bir takvim basmaz.

`store.put_sprint_round(season, round)` ile işaretlenir.


## Seans durumu: "bitti" ne zaman doğru

İki ayrı soru tek kelimeyle cevaplanıyordu ve ikisi de yanlış cevap üretti:

| Gerçek durum | Eskiden ne diyordu | Şimdi |
|---|---|---|
| Meksika yarışı henüz başlamadı (`start_date` ileride, 3 ıskarta çerçeve var) | **bitti** | `başlamadı` |
| Avustralya GP bitti, kayıt 57/58'de kesik | **bitmedi, damalı bayrak görünmüyor** | `bitti · kayıt 57/58'te kesiliyor` |

`weekend.race_phase()` bu ayrımı yapar ve iki bayrak döner: **`event_over`**
("geçmiş zamanda mı anlatılmalı?") ve **`record_complete`** ("bu kayıt bir sonucu
destekler mi?"). Kart, arşiv rozeti ve brifing satırı hepsi AYNI fonksiyonu okur.

Kanıt sırası (saat en sonda, çünkü `SessionInfo.StartDate` pistin yerel saatidir,
UTC değil — Meksika FP1 12:30 yazıyor, OpenF1 18:30 UTC diyor):

1. `start_date` belirsiz biçimde ilerideyse → **başlamadı**. 12 saatlik pay
   (`_TZ_MARGIN_HRS`) bütün gerçek saat dilimi farklarını kapsar; ıskarta bir
   çerçeve bunu geçmişe çekemez.
2. Hiç kayıt yoksa → **veri yok**.
3. Arşivden doldurulmuş seansın bitiş zamanı geçmişse → **bitti**. Ayna bitmiş
   seansı güncellemez.
4. `final_status` bitmişse **veya** yarış mesafesi tamamlanmışsa → **bitti, tam**.
5. Kayıt son 90 sn içinde güncellendiyse → **sürüyor**. Ama yalnız yazan süreç
   `source=live` ise: `upsert_race` `last_seen`'i yazma anıyla damgaladığı için
   tazelik tek başına dört doldurulmuş antrenmanı "şu an canlı" gösteriyordu.
6. Bayat kayıt + kesin geçmiş başlangıç → **bitti**, kayıt kesikse kesik.

Brifinge de yazılır (`SEANS DURUMU: ... | KAYIT ...`), çünkü model yalnız
`status=Started` + `tur=57/58` görünce "henüz bitmedi" diyordu.

## Geçmiş hafta sonlarının eksik seansları

Canlı yazıcı yalnız o an koşan seans için satır açar; yakalama kapalıyken geçen
antrenman/sıralama oturumlarının arşivde satırı hiç yoktu ("neden antrenman verisi
yok" sorusu). `tools/backfill_openf1.py --weekend <meeting_key>` (veya
`--weekends-season 2026`) eksik seansları OpenF1'den ekler ve doldurur; **var olan
satıra dokunmaz** (canlı kayıt daha zengin). 2026 sezonu için 68 seans eklendi.

İki tuzak ölçüldü ve kapatıldı:

- Çerçeve üreticisi `session: "Race"` **sabitti**, yani doldurulmuş FP1 brifinge
  "seans_tipi=Yarış (race) | tur=26/26" diyordu. Artık `session_ctx` taşınıyor,
  ayrıca `_decorate()` kimliği **`races` satırından** zorluyor — yazıcıyı
  düzeltmek yalnız yeni çerçevelere yarar, 68 seans zaten yazılmıştı.
- `source=openf1` + taze `last_seen` "canlı" sanılıyordu (yukarıdaki 5. madde).

## Kapsam: hangi seanslar bağlama girecek

Sohbet bir **seansa** bağlıdır, ama "üç antrenman bitti, favori kim" hafta sonuna
dair bir sorudur. İki uç reddedildi: her seansın tam brifingini bağlama koymak
(~6 × 4.4k token, üstelik seans-tipi kuralları çakışır) ve LLM araç döngüsü
(soru başına 20-30 sn, ~13k token, argüman uydurma riski).

`scope.py` soruyu LLM'den **önce** çözer — `routing.py`'nin deseni:

| Öncelik | Kaynak | Not |
|---|---|---|
| 1 | UI çipleri (`scope: [...]`) | insan kararı; tahmin yok |
| 2 | `@kısayollar` | açık niyet |
| 3 | sahibi seans | bugünkü davranış, birebir |

**Dördüncü adım yok** (cümleden niyet çıkarımı): karşılama ızgarası kapsamı açıkça
gönderebildiği için gereksiz risk.

| Kısayol | Seans |
|---|---|
| `@fp1` `@fp2` `@fp3` | Antrenman 1/2/3 |
| `@q` | Sıralama |
| `@sq` | Sprint sıralaması |
| `@sp` | Sprint |
| `@race` | Yarış |
| `@all` / `@w` | Hafta sonu özeti |

`@p1` **bilinçli olarak yok**: bu panelde P1 her yerde "birinci sıra" demek.
Tanınmayan `@xyz` sessizce yutulmaz, cevabın meta'sında bildirilir. Token
transkripte yazılmaz (temizlenmiş soru saklanır), yoksa sonraki turun geçmişi
kapsam isteği gibi okunup aynı bloklar iki kez çekilir.

Kapsam sahibi seansı içermiyorsa **temel anlık görüntü de değişir**: ölçüldü,
yarış sohbetinde `@fp1 @fp2` sormak yarış temposu cevabı veriyordu çünkü yarış
çerçevesi prompt'ta ilk sıradaydı. Artık kapsamdaki ilk elde olan seans temel olur.

Cevabın üstünde görünür: `Veri: Antrenman 1, Antrenman 2`. Yanlış kapsam sessizce
yanlış cevap üretir; satır görünürse hemen fark edilir.

## AI sağlayıcı seçimi

**Hiçbir sağlayıcı gömülü değildir.** Depoyu klonlayan kişi kendi base URL'ini,
modelini ve anahtarını girer; girmezse asistan "sağlayıcı tanımsız" der ve dışarı
hiçbir istek atmaz. Sohbet başlığındaki **⚙ AI** düğmesi (`ai_config.py`,
`static/shell.js`).

- Profil = (etiket, base_url, model, api_key); OpenAI uyumlu `/chat/completions`.
  OpenAI, OpenRouter, Groq, Together, yerel Ollama/llama.cpp — hepsi aynı arayüzü
  konuştuğu için tek kod yolu var.
- Öncelik: **seçili profil → `F1_AI_KEY` / `F1_AI_BASE` / `F1_AI_MODEL` ortam
  değişkeni → yok**.
- **Anahtar tarayıcıya asla dönmez.** `_row_public()` yalnız `has_key` ve bir
  **SHA-256 parmak izi** (`sha:29b1d4`) döndürür. İlk sürüm anahtarın son 4
  karakterini "ipucu" diye veriyordu — bu sırrın gerçek bir parçasıdır, ekran
  görüntüsüne ve log'a düşer; sızdığı an anahtar yanmış sayılır.
- Boş anahtar alanı **kayıtlıyı silmez** (form boş parola gönderdiğinde kimlik
  bilgisi kaybolmasın diye) ve DB dosyası yazımdan sonra 0600'e çekilir.
- Sağlayıcı hata gövdesinde anahtarı geri yansıtabilir ("invalid api key: sk-…");
  bu metin `scrub()` ile maskelenmeden ekrana ve log'a çıkmaz.
- "Bağlantıyı test et" kaydetmeden önce tek token'lık bir çağrı yapar: yazım
  hatası ilk gerçek soruda 6-110 sn bekleyip anlaşılmaz bir sağlayıcı hatası
  olarak dönmesin.
- İki sohbet yüzeyi ayrı süreç olduğu için `/api/ai` ikisinde de mount edilir;
  ayar ortak veritabanında durur.

### base_url neden doğrulanıyor (SSRF)

`base_url` kullanıcı girdisidir ve **sunucu o URL'ye istek atar** — klasik SSRF
şekli. `check_base_url()` her kaydetme ve test çağrısında çalışır:

| hedef | sonuç |
|---|---|
| `http://127.0.0.1:11434/v1`, `http://localhost:8080/v1` | ✅ yerel model, beklenen kurulum |
| `https://api.openai.com/v1`, herkese açık https | ✅ |
| `http://169.254.169.254/...`, `metadata.google.internal` | ❌ bulut meta verisi |
| `http://10.x`, `http://192.168.x`, `http://172.16-31.x` | ❌ (aşağıdaki bayrakla açılır) |
| `file://`, `ftp://`, `gopher://` | ❌ şema reddedilir |
| link-local, multicast, reserved, unspecified IP | ❌ |

Kontrol iki katmanlı: URL'deki düz IP **ve** hostname'in çözüldüğü adresler —
`evil.example` her yere işaret edebilir. Özel ağdaki kendi sunucunda
çalıştırıyorsan `F1_AI_ALLOW_PRIVATE=1` ile RFC1918'i açabilirsin; loopback her
zaman açık.


## Sohbet neden akış (SSE)

Şikâyet: *"İstek hatası: HTTP 524 — sunucu JSON yerine HTML döndürdü"*. **524
standart bir HTTP kodu değildir**; onu bir CDN/vekil üretir ve tek anlamı vardır:
*"arkadaki sunucudan zamanında cevap alamadım, bağlantıyı kestim."*

Kök neden mimariydi. Normal `POST /api/chat` çağrısı model düşünürken **30-110
saniye boyunca tek bayt göndermiyordu**. Bağlantı canlı, istek gitmiş, ama sessiz.
Baytsız kalan bağlantıyı bir aracı ~100 saniyede keser ve yerine kendi HTML hata
sayfasını basar; panel bunu "JSON yerine HTML" olarak görür. Cevap üretilmiş (ve
ödenmiş) olduğu hâlde kullanıcıya hiç ulaşmazdı.

Düşünme seviyesi "yüksek"te bu sessizlik kolayca sınırı aşıyordu. Kurtarma yolu
(düşünmeyi kapatıp tekrar sormak) ikinci bir çağrı demek olduğu için süreyi daha da
uzatıyor, 524 ihtimalini artırıyordu.

### Çözüm: semptomu değil sebebi kaldır

`POST /api/chat/stream` (canlı 8099 ve arşiv 8100'de ayrı ayrı, çünkü ayrı
süreçler; hat `chat_request.handle_stream` tek yerde):

| Olay | İçerik |
|---|---|
| `scope` | cevabın hangi seanslara dayandığı |
| `start` | düşünme seviyesi + model |
| `thinking` | düşünme sürüyor (yalnız sayaç; metin gösterilmez) |
| `delta` | cevabın bir parçası — geldikçe eklenir |
| `done` | nihai cevap + maliyet/kalite meta'sı |
| `error` | cevap yerine gösterilecek mesaj |

Üç ayrı şey bağlantıyı canlı tutuyor:

1. **Tokenlar.** Cevap yazıldıkça bayt akar; asıl çözüm bu.
2. **Kalp atışı.** Düşünme tokenları *içerik* olarak gönderilmez, yani
   düşünme ağır bir soruda bağlantı yine sessiz kalırdı. `CHAT_PING_SECS = 5`
   ile SSE yorum satırı (`: ping`) gider — EventSource ve kendi ayrıştırıcımız
   bunu yok sayar, cevabı bozamaz. 5 sn, bir aracının tipik ~100 sn'sinin yirmide biri.
3. **Düşünme olayları** 200 karakterde bir seyreltilmiş: panelin "düşünüyor…"
   sayacı görünür şekilde ilerler, ama cevap başına yüzlerce SSE olayı üretilmez.

### Neden `EventSource` değil

`EventSource` yalnız GET yapabilir ve gövde gönderemez; soruyu, kapsamı ve turu
taşımak gerekiyor. Bu yüzden `fetch()` + elle okuyucu (`static/shell.js`
`chatStream`). Ayrıştırıcı bilerek küçük: sunucumuz tam olarak `data: <json>` ve
`: ping` üretiyor.

### Akış yolunun kilitlediği üç davranış

- **İstek gövdesi tek yerden.** `analysis._request_body` hem akışlı hem akışsız
  yolu üretir; model/bütçe/düşünme ayarı ikisinde ayrışamaz.
- **Meta tek yerden.** `analysis._answer_meta`; maliyet satırı iki yolda farklı
  olamaz.
- **Hata transkripte cevap olarak yazılmaz.** Başarısız çağrı `done` üretmez;
  "(boş cevap)" saklanırsa sonraki turlar asistanın cevapladığını sanır.

Ölçülen sonuç: cevabın ilk kelimesi **0.3 sn**'de ekranda (eskiden 30-110 sn
sessizlik). `tests/test_stream.py` istek gövdesini, meta paritesini, çift-ekleme
regresyonunu, tarayıcı ayrıştırıcısını (ikiye bölünmüş JSON, ping, hata olayı) ve
kalp atışı aralığını sınıyor.

## Sektör renkleri

Tablolarda S1/S2/S3, IDEAL ve LOSS kolonları var; sektörler F1 renk kuralıyla
boyanır: **mor** = seansta sahanın en hızlısı, **yeşil** = sürücünün kendi en
iyisi, **sarı** = kendi en iyisinden yavaş.

Kararı **sunucu** verir (`analysis._sector_status`), tarayıcı yalnız sınıfı basar.
İkinci bir karşılaştırma, hemen yanındaki IDEAL/LOSS aritmetiğiyle çelişmeye
başlardı. Renk tek başına yetmez (renk körlüğü, güneş ışığı): hücrenin sol
kenarına aynı rengi taşıyan çizgi konur ve değer her zaman yazılı kalır.

## Hangi ajan nerede

Her brifingin ikinci satırı `ORTAM:` der: canlı yakalama mı, arşiv mi, yoksa canlı
yarışın geçmiş bir turu mu. Bu satır olmadan model canlı yarışı kayıtla karıştırır ve
"geçen yarış" sorusunu yanlış yarıştan cevaplar.

Model araç çağıramaz (tek istek/tek cevap). Bu yüzden "arşive bak" niyeti modele
bırakılmaz: `routing.py` soruyu LLM çağrısından **önce** çözer.

| Soru | Ne olur |
|---|---|
| "tur 20'de ne olmuştu" | o turun gerçek satırları + sektörleri brifinge eklenir |
| "geçen yarışta ne oldu" | arşivdeki o yarışın son tur özeti eklenir |
| adı geçen yarış arşivde yok | arşiv listesi eklenir, model **hangi yarış** diye sorar |

Tool loop kurulmadı: ölçüldü, bir LLM çağrısı 6-10 sn ve brifing ~4.4k token; üç
adımlı bir loop soru başına 20-30 sn ve ~13k token eder, reasoning kapalı bir modelde
araç argümanı uydurma riskiyle birlikte. Yönlendirme deterministik ve test edilebilir.

## Testler

```bash
./run.sh test                                              # dördü birlikte
.venv/bin/python -m unittest discover -s tests -v          # sadece Python birim
node tests/ui_test.js                                      # tarayıcı tarafı (DOM taklidi)
node tests/layout_test.js --shots /tmp/shots               # gerçek tarayıcı + görüntü
.venv/bin/python tests/smoke_real_db.py                    # sadece duman
.venv/bin/python tests/smoke_real_db.py --live --standings  # servisleri de yokla
.venv/bin/python tests/smoke_real_db.py --fetch             # gerçek API'den de çek
node tools/shot.js URL ÇIKTI.png 412 915 3000 --click "#grip"  # tek ekran görüntüsü
```

`tests/test_points.py` saf aritmetiği sınar (tablo değerleri, DNF, sprint tablosu,
projeksiyon, eşit puan, tabanda olmayan sürücü, 22 sürücülük tam yarış).

`tests/ui_test.js` sayfaların **gerçek kaynağını** alıp küçük bir DOM taklidi
üzerinde çalıştırır (39 kontrol): mobil panel aç/kapa ve tutamak önizlemesi,
klavye yüksekliği telafisi, `fetchJSON`'un HTML gövdesini sızdırmaması, nav
linklerinin iki erişim şeklinde de doğru olması, tablo başlık/hücre sırası,
markdown biçimlendirme sırası (XSS), panel CSS önceliği.

`tests/layout_test.js` **gerçek tarayıcıda ölçer** (15 kontrol): panel 46px'e
kilitli mi, tutamaç flex ile eziliyor mu, açık panel %62'de mi, klavye açılınca
yukarı itiliyor mu, `[hidden]` gerçekten gizliyor mu. CSS önceliği, flex
ezilmesi ve yüzde hesabı gibi hatalar DOM taklidinde görünmez — bu turda üç
hata tam olarak böyle kaçmıştı.

Tarayıcı: Chrome for Testing'in `chrome-headless-shell` ikilisi, çünkü bu
makinede `/snap/bin/chromium` ve `firefox` snap-confine yüzünden çalışmıyor.
Kurulum bir kez:

```bash
mkdir -p ~/.local/share/f1-agentic-analyst/tools && cd ~/.local/share/f1-agentic-analyst/tools
curl -sLO https://storage.googleapis.com/chrome-for-testing-public/131.0.6778.85/linux64/chrome-headless-shell-linux64.zip
unzip chrome-headless-shell-linux64.zip
```

İkili yoksa veya servis ayakta değilse test kendini atlar (kırmızı vermez).

`tests/smoke_real_db.py` gerçek `data/f1dash.db` dosyasını **geçici bir kopyaya**
kopyalar ve her şeyi kopya üzerinde yapar; canlı arşive yazmaz. Sınadığı şey "birim
testler geçiyor" değil, "elimizdeki arşivde çalışıyor": şema göçü, önbellek gidiş-dönüşü,
en yeni gerçek yarışın sırasıyla projeksiyon ve **bağımsız yeniden hesap** (her satırda
`total = taban + yarış puanı`, puanlanan her sürücünün puanı sırasının tablo değeri),
çift sayma koruması.

### Yüzeyleri elle yoklamak

Burada hiçbir şey gerçek veritabanına ya da yarış sürüyor olmasına bağlı değil:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8099/api/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/api/races    # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8101/api/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8103/             # 200
timeout 3 curl -sN http://127.0.0.1:8099/api/stream | head -c 120            # data: ...
```

Nav mantığı hem `127.0.0.1`'den hem bir LAN adresinden açıldığında doğru adresi
üretmeli: makine adı her zaman `location`'dan gelir, sabitten değil.
`tests/ui_test.js` bunu `shell.js`'i sahte bir `location`/`document` ile
çalıştırarak sınıyor.

## Tasarım kaynağı

Arayüzün ilk hâli bir tasarım prototipinden türedi: masaüstü puan durumu, canlı +
geçici puan paneli ve mobil puan durumu ekranları. Prototip **görselleri depoda
tutulmaz** — içlerinde gerçek yarış verisi (tur süreleri, FIA mesajları, puan
tablosu) vardı ve o veri bu deponun lisansı altında yeniden dağıtılamaz. Geriye
yalnız sayfaların kullandığı CSS token'ları kaldı (`static/theme.css`).

## Sohbet kalıcılığı

Sohbet `chat` tablosunda, `race_id` ile anahtarlanmış. Sayfa yenilemek, tarayıcı verisini
temizlemek, hatta başka tarayıcıdan açmak sohbeti silmez. Aynı yarışın sohbeti iki
yüzeyde de aynıdır — hangi arayüzden açtığınız fark etmez.

## Öğrenilen tercihler (asistan kendi kendine öğrenir)

Süre biçimi, sunum biçimi, dil ve düşünme seviyesi **tercih** olarak `prefs` tablosunda
tutulur. Bunlar yarışa değil kişiye ait olduğu için yarış anlık görüntüsünün içine
gömülmez ve tarayıcının `localStorage`'ında da durmaz: tarayıcı verisi silinince tercih
kaybolmasın, iki yüzey aynı biçimde cevap versin.

Asistan bunları sohbetten öğrenir — model çağrısı yok, deterministik regex tablosu
(`analysis._LEARN_RULES`), yani asistan kendi kendine tercih uyduramaz:

| Yazdığınız | Öğrenilen |
|---|---|
| "süreyi dakika saniye olarak istiyorum" | `time_format=mmss` → `1:38.993` |
| "bundan sonra saniye olarak ver" | `time_format=sec` → `98.993` |
| "liste halinde" / "madde madde" | `answer_style=list` |
| "tablo" / "düz cümle" | `answer_style=table` / `prose` |
| "ingilizce" / "türkçe" | `language=en` / `tr` |
| düşünme menüsünü değiştirmek | `reasoning=off…high` |

Öğrenme **cümle cümle** çalışır: "tek pit stratejileri nasıl çalıştı, liste halinde dakika
saniye olarak" mesajının ilk cümlesi veri sorusudur (hiçbir şey öğretmez), ikincisi biçim
talimatıdır. Öğrenilen tercih **aynı cevaba** uygulanır, sonrakine değil. Sohbet
penceresindeki yeşil `tercih:` rozeti ne öğrenildiğini gösterir, tıklayınca sıfırlanır
(`POST /api/prefs {"reset":true}`). Tercih brifinge `TERCİH:` satırı olarak girer ve süreler
LLM'e **zaten istenen birimde** verilir — modelin biçim çevirmesine güvenilmez.

## Tur verisi: hepsi var, "son 8 tur" sadece bir pencere

`lap_times` tablosu **her sürücünün her turunu** tutar (ABD GP 2026: 22 sürücü, 1163 tur
kaydı; eksikler yalnızca yarış dışı kalanlar ve son tur — brifinge "kayıt yok: tur …"
olarak yazılır). Buna rağmen eski brifing yalnızca `LAST` / `BEST` / `MED_last8` / `TREND`
gönderiyordu, bu yüzden asistan "bütün turlar elimde yok" diyordu — veri vardı, bağlamda
yoktu.

Artık brifing `TÜM TURLAR` bloğunu taşır: sürücü başına stint sınırlarıyla (`[HARD 40-56]`)
tek tek tur süreleri (~16 KB brifing, ~4k token). `MED_last8` = son 8 turun medyanı ve
`TREND` = o pencerenin en yenisi eksi en eskisi; ikisi de **kısayol**, veri sınırı değil.
Bağlam bütçesi aşılırsa blok sürücü başına son 15 tura iner ve bunu başlıkta söyler.

Ayrıca yarış dışı kalan sürücüler artık son karede olmasalar da `lap_times` + en yeni
karelerinden kimliklendirilip tabloya eklenir (`store.race_driver_meta`), böylece
brifingden tamamen düşmezler.

Brifing satırı, sektör verisi olan turlarda `S1 S2 S3 IDEAL LOSS` kolonlarını da taşır
(`analysis._attach_sectors`): IDEAL = sürücünün kendi en iyi sektörlerinin toplamı,
LOSS = gerçek tur − IDEAL. Sektör yayınlanmamışsa `-` yazılır, uydurulmaz; böylece
"nerede zaman kaybediyor" sorusu tahmine değil bölünmüş süreye dayanır.

## Maliyet görünürlüğü: token, context, kesilme, kredi

Her cevabın altındaki meta satırı ve her yüzeyin altındaki ince şerit, çağrının gerçek
maliyetini gösterir. Hiçbir sayı tahmin değil: hepsi sağlayıcının `usage` bloğundan gelir.

```
düşünme: off · context 12.8k · cache %5 · cevap 1400/1400 · geçmiş 6/24 · KESİLDİ
```

| Alan | Anlamı |
|---|---|
| `context` | `prompt_tokens` — brifing + sohbet geçmişi + sorunun toplamı |
| `cache %` | `cached_tokens / prompt_tokens`; brifing her turda yeniden kurulduğu için yüksek çıkar (maliyeti düşürür) |
| `cevap n/max` | `completion_tokens` / bütçe — düşünme açıkken `MAX_TOKENS_THINKING` (8000), kapalıyken `MAX_TOKENS` (1400) |
| `geçmiş s/t` | geçmişin kaçta kaçının gönderildiği — compact politikası: son `HIST_TURNS=6` mesaj, her biri `HIST_CHARS=2000` karakter |
| `KESİLDİ` | `finish_reason == "length"` → cevap limitte kesildi |
| `düşünme: off (bütçe yetmedi)` | düşünme bütçesi cevap yazmaya yetmedi, hızlı modda tekrarlandı |

Kesilme eskiden **görünmezdi**: cevap cümle ortasında biterdi ve bu model hatası gibi
okunurdu. Artık üç yerde görünüyor — meta satırında `KESİLDİ`, cevabın altında sarı uyarı,
ve `/api/usage` içinde `truncated` sayacı.

### Düşünme jetonları cevap bütçesini yer — ve tavan arızanın sebebiydi (ölçüldü)

Sağlayıcı düşünme jetonlarını `completion_tokens`'ın **içinde** faturalıyor, üstüne
eklemiyor. Yani tek bir `max_tokens` hem düşünmeyi hem cevabı kapsıyor. 1400 tek başına
buna yetmiyordu: model 1400'ün tamamını düşünerek bitirip **boş cevap** döndürüyordu.

İlk çözüm tavanı büyütmekti (1400 → 3000). Yanlıştı. Tavanı iyice yükseltip
(`max_tokens=16000`) ölçünce modelin kendiliğinden durduğu görüldü:

| Soru | ct | rtok | bitiş | süre |
|---|---|---|---|---|
| "ikinci stint neden kötüydü" | 1350 | 920 | `stop` | 39.9 sn |
| aynı soru (tekrar) | 590 | 482 | `stop` | 31.6 sn |
| "tek tek pit yapsa kazanabilir miydi" | 4962 | 3774 | `stop` | 132.0 sn |
| "LEC'in stint 1'i neden yavaştı" | 1350 | 546 | `stop` | 34.0 sn |

Dördü de doğal olarak durdu. Doğal ihtiyaç 590–4962 jeton arasında değişiyor; ortalamaya
göre ayarlanmış bir tavan **kuyruğu** keser. Yani 3000'lik tavan, engellemeye çalıştığı
boş-cevap arızasının sebebiydi: "tek tek pit yapsa kazanabilir miydi" 4962 jeton istiyor,
3000'de tamamı düşünmeye gidip cevaba yer kalmıyordu.

Tavan artık **8000** (`MAX_TOKENS_THINKING`) ve yalnız **kaçak cevap koruması** — gözlenen
maksimumun (4962) belirgin üstünde. Bir çağrıyı sınırlayan şey tavan değil, süre
(`THINK_TIMEOUT=300`). Ölçüm sonrası iki zor soru kesilmeden, kurtarma devreye girmeden
cevaplandı (1684 ve 2521 jeton).

Bedeli: "tek tek pit yapsa kazanabilir miydi" 67–132 sn sürebiliyor. Hız önemliyse
"düşünme: kapalı" 2–3 sn'de cevap verir.

İki güvenlik ağı duruyor:

1. **Boş cevap kurtarma.** `finish_reason=length` + boş içerik gelirse istek bir kez
   `reasoning_effort=none` ile tekrarlanır. Tavan 8000'e çıkınca ölçülen çağrılarda hiç
   devreye girmedi (kurtarma oranı %15 → %0); kaçak bir cevap için duruyor. Meta
   `düşünme: off (bütçe yetmedi)` der, iki çağrının jetonları toplanır (`_merge_usage`),
   yani maliyet olduğundan az görünmez.
2. **"devam et" uyarısı dürüst.** `cutNote()` yalnız gerçekten kesilmiş metin varsa devam
   teklif eder; boş cevapta yarıda kalmış cümle olmadığı için soruyu daraltmayı önerir.

`GET /api/usage` (iki yüzeyde de aynı, tek `usage` tablosu) son 20 cevabın özetini verir:
`calls`, `avg_prompt`, `prompt_tokens`, `cache_pct`, `completion_tokens`, `truncated`,
`credits_cr` (sağlayıcının bildirdiği kalan kredi) ve ham `last` satırı. Kredi sayısı
panelde görünür, böylece kota bitmesi sürpriz olmaz.

Satırlar `usage` tablosunda tutulur (`store.usage_add` / `store.usage_summary`), çünkü
"bu yarış bana kaça mal oldu" bir toplama sorusudur ve servis yeniden başlayınca da
cevabı olmalı.

## Lisans, kaynaklar ve yasal notlar

**Kod: MIT** — bkz. [LICENSE](LICENSE). Ticari kullanım dahil her şeye izin verir;
tek koşul telif satırını korumak. Çalışma zamanı bağımlılıkları da izin verici
lisanslıdır (FastAPI: MIT, uvicorn/websockets/Starlette: BSD-3-Clause), yani MIT ile
uyumludurlar.

### Veri kaynakları ve lisansları

Bu proje **hiçbir F1 verisini kendi deposunda taşımaz**: ne örnek veritabanı, ne
JSON dökümü, ne statik istatistik sayfası. `data/` .gitignore'da; klonlayan kişi
veriyi kendi çalıştırdığı anda kendi makinesinde toplar. Lisans soruları bu yüzden
**kodun** değil, **veri kaynağının** lisansına bağlıdır ve kaynakların koşulları
şunlardır:

| Kaynak | Kullanım | Lisans / koşul | Ne yapar |
|---|---|---|---|
| F1 resmî canlı timing (`livetiming.formula1.com`) | WebSocket aboneliği | Formula 1'in kullanım koşullarına tabidir; resmî/ticari bir API değildir | Canlı yakalama (`server.py`) |
| [Jolpica F1](https://github.com/jolpica/jolpica-f1) (`api.jolpi.ca`) | Sıralama/puan verisi | **Apache-2.0** (Ergast API'nin devamı) | Puan durumu |
| [OpenF1](https://openf1.org/) (`api.openf1.org`) | Geçmiş seans tamamlama | **CC BY-NC-SA 4.0** — yalnız **ticari olmayan**, paylaşım aynı lisansla | `tools/backfill_openf1.py`, `tools/audit_coverage.py` |

**En önemli nokta:** OpenF1 verisi ticari kullanıma kapalıdır. Bu depo kod olarak
MIT olsa da, **OpenF1'den çekilmiş verinin ticari bir üründe kullanılması bu depo
tarafından verilemez** — o verinin lisansı CC BY-NC-SA 4.0'dır ve NC kısıtı
kaynaktan gelir. Kendi kurulumunda yalnız kişisel/öğrenme amaçlı kullanım bunun
dışındadır. Ticari bir ürün kuracaksan OpenF1 ile ayrıca lisans görüşmesi yapmalısın
(openf1.org'da "contact" bağlantısı var).

F1 canlı timing WebSocket'i de resmî bir API değildir; Formula 1'in kullanım
koşulları ve telif hakları geçerlidir. Kişisel kullanım dışında kullanacaksan
F1 ile lisans anlaşman gerekir.

### Ticari marka

Proje adı ve arayüzdeki takım/sürücü adları yalnız **tanımlayıcı** amaçla
kullanılmaktadır. "F1", "FORMULA ONE", "FORMULA 1", "FIA FORMULA ONE WORLD
CHAMPIONSHIP", "GRAND PRIX" ve ilgili işaretler **Formula One Licensing B.V.**'nin
ticari markalarıdır. Bu proje Formula 1, FIA veya Formula One Management ile
**ilişkili, onlar tarafından desteklenen veya sponsorlanan bir proje değildir**.
Lastik bileşeni adları (SOFT/MEDIUM/HARD) ve renkleri de F1/Pirelli işaretlerine
gönderme yapar; bunlar yalnız veri görselleştirmesinde kullanılır.

Depoda F1 logosu, yayın görüntüsü, takım logosu veya telifli fotoğraf **yoktur**.
Tasarım prototipinin ekran görüntüleri de bu yüzden **depodan çıkarıldı**: gerçek
yarış verisi ve FIA race-control metni içeriyorlardı. Kalan tek görsel
`design/pwa-icon.svg` — projenin kendi çizdiği, hiçbir üçüncü taraf içerik
taşımayan uygulama ikonu; `tools/make_icons.py` ondan PNG üretir.

### Eğer ticari bir ürün yapacaksan — kontrol listesi

1. OpenF1 verisini üründen çıkar ya da OpenF1 ile ticari lisans anlaş.
2. F1 canlı feed'ini F1 ile lisans anlaşmadan ticari olarak kullanma.
3. "F1" ibaresini ürün adı/marka olarak kullanma; tanımlayıcı kullanımda bile
   karışıklık yaratacak biçimde konumlandırma (ör. logo, resmî görünüm).
4. Takım/sürücü adları olgusal veri olarak kullanılabilir; logolar kullanılamaz.
