#!/usr/bin/env node
/* Hafta sonu yüzeyi (welcome.html) — tarayıcı tarafı davranışı.
 *
 * Bu makinede başsız tarayıcı çalışmadığı için (bkz. ui_test.js) sayfanın GERÇEK
 * kaynağı alınıp küçük bir DOM taklidi üzerinde çalıştırılıyor. Sınanan şeyler,
 * her biri geçmişte gerçekten kırılmaya açık olduğu için:
 *
 * 1. Kart açılabilirliği: kaydı olmayan bir seans <a> olmamalı. Tıklanınca
 *    hiçbir şey yapmayan bir bağlantı, kırık bir sayfa gibi okunur.
 * 2. "tur" ile "tur kaydı" ayrımı: `laps` lap_times SATIR sayısıdır, yarış turu
 *    değil. Yanlış kelime en görünür yerde yanlış sayı üretiyordu.
 * 3. Durum rozeti kısa olmalı: uzun etiket 168px kartta seans adıyla aynı satıra
 *    sığmıyor ve sayıyı aşağı itiyordu.
 * 4. LIVE rozeti yalnız canlı kartta; ikinci bir LIVE etiketi durum hatasıdır.
 * 5. Derin bağlantı: kartın href'i arşivin ?rid= biçimini kullanmalı ki tıklama
 *    doğru seansı açsın (arşiv tarafı bunu okuyor, bkz. archive.html boot()).
 *
 * Çalıştırma: node tests/welcome_test.js   (run.sh test bunu da çağırır)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");
const read = (f) => fs.readFileSync(path.join(ROOT, f), "utf8");

let bad = 0, n = 0;
async function t(name, fn) {
  n++;
  try { await fn(); console.log("  ok   " + name); }
  catch (e) { bad++; console.log("  HATA " + name + ": " + e.message); }
}
function section(title) { console.log("\n" + title); }

/* Sayfadan bir kod aralığı keser: tamamını çalıştırmak fetch/EventSource ister. */
function slice(src, from, to) {
  const i = src.indexOf(from);
  if (i < 0) throw new Error("başlangıç bulunamadı: " + from.slice(0, 40));
  const j = src.indexOf(to, i);
  if (j < 0) throw new Error("bitiş bulunamadı: " + to.slice(0, 40));
  return src.slice(i, j);
}

/* cardFor() tek başına çalıştırılabilsin: esc/surface/F1 bağımlılıkları verilir. */
function makeCardFor(src) {
  const body = slice(src, "function surface(port, query)", "function renderCards(ov)");
  const escFn = (t) => String(t == null ? "" : t)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const F1 = {
    esc: escFn,
    surfaceHref: (p) => "http://127.0.0.1:" + p + "/",
    surfaceUrl: (p) => "http://127.0.0.1:" + p + "/",
    whenText: (s) => String(s || "—"),
  };
  const S = { ports: { live: "8099", archive: "8100" } };
  return new Function("F1", "S", "esc", body + "\nreturn cardFor;")(F1, S, escFn);
}

const CARD = (over) => Object.assign({
  kind: "FP1", label: "Antrenman 1", rid: 19, state: "done", state_label: "tamamlandı",
  live: false, laps: 106, frames: 1, coverage: 1, thin: true, chat: 12,
  best: 79.2, best_driver: "VES", headline: "1:19.200 VES", start_date: "",
}, over || {});

(async function main() {
  const src = read("welcome.html");

  section("1. kart açılabilirliği");
  const cardFor = makeCardFor(src);

  await t("kayıtlı seans bağlantı oluyor", () => {
    const h = cardFor(CARD());
    if (!h.startsWith("<a ")) throw new Error(h.slice(0, 60));
    if (!/href="http:\/\/127\.0\.0\.1:8100\/\?rid=19"/.test(h)) throw new Error("href yanlış: " + h.match(/href="[^"]*"/));
  });

  await t("kaydı olmayan seans bağlantı DEĞİL", () => {
    for (const st of ["missing", "scheduled", "empty"]) {
      const h = cardFor(CARD({ state: st, rid: null, laps: 0, frames: 0, best: null, headline: "" }));
      if (h.startsWith("<a ")) throw new Error(st + " hâlâ <a>");
      if (!/aria-disabled="true"/.test(h)) throw new Error(st + " aria-disabled yok");
      if (/href=/.test(h)) throw new Error(st + " href taşıyor");
    }
  });

  await t("canlı seans canlı yüzeye gidiyor", () => {
    const h = cardFor(CARD({ state: "live", live: true, laps: 48 }));
    if (!/href="http:\/\/127\.0\.0\.1:8099\/"/.test(h)) throw new Error(h.match(/href="[^"]*"/));
    if (!/class="badge-state live"/.test(h)) throw new Error("LIVE rozeti yok");
  });

  await t("canlı olmayan kartta LIVE rozeti yok", () => {
    const h = cardFor(CARD());
    if (/LIVE/.test(h)) throw new Error("bitti kartında LIVE var");
  });

  section("2. durum rozeti kısa ve sayı görünür");
  await t("uzun etiket ('tamamlandı') rozete basılmıyor", () => {
    const h = cardFor(CARD());
    // The badge's TEXT must be the short word; the long label may only appear in
    // the title attribute (and in the sub line for empty states).
    const m = h.match(/<span class="badge-state[^"]*"[^>]*>([^<]*)<\/span>/);
    if (!m) throw new Error("rozet bulunamadı");
    if (m[1].trim() !== "bitti") throw new Error("rozet metni: " + m[1]);
    if (!/title="tamamlandı"/.test(h)) throw new Error("uzun etiket title'da yok");
  });

  await t("headline'da 'az tur' eki yok (kartta kırpılıyordu)", () => {
    const h = cardFor(CARD({ headline: "1:19.200 VES" }));
    const hl = h.match(/class="hl">([^<]*)</);
    if (!hl) throw new Error("headline bulunamadı");
    if (/az tur/.test(hl[1])) throw new Error("ek headline'da: " + hl[1]);
    if (!/az tur: tempo değil/.test(h)) throw new Error("uyarı alt satırda yok");
  });

  section("2b. etiket rozetle çakışmıyor (gerçek telefonda çakıştı)");
  await t("başlık ve rozet ayrı grid hücrelerinde", () => {
    const h = cardFor(CARD());
    // Measured on a phone: a hard-coded padding-right let the badge sit ON the
    // label ("ANTRENM…KAYIT YOK"). Only a layout column sized by content is
    // safe for every label/badge pair.
    if (!/<div class="head">/.test(h)) throw new Error("head kabı yok");
    const head = h.match(/<div class="head">([\s\S]*?)<\/div>/);
    if (!head) throw new Error("head içeriği yok");
    if (!/class="k"/.test(head[1])) throw new Error("etiket head içinde değil");
    if (!/badge-state/.test(head[1])) throw new Error("rozet head içinde değil");
  });
  await t("kart etiketi kısa (uzun 'Antrenman 1' 168px'e sığmıyor)", () => {
    const h = cardFor(CARD({ label: "Ant. 1" }));
    const k = h.match(/class="k">([^<]*)</);
    if (!k) throw new Error("etiket yok");
    if (k[1].length > 10) throw new Error("etiket uzun: " + k[1]);
  });

  section("3. sayı sözcükleri dürüst");
  await t("'tur kaydı' yazıyor, 'tur' değil", () => {
    const h = cardFor(CARD({ laps: 1163 }));
    if (!/1163 tur kaydı/.test(h)) throw new Error(h);
    if (/\b1163 tur\b/.test(h.replace("1163 tur kaydı", ""))) throw new Error("çıplak 'tur' var");
  });

  section("4. sayfa kaynağı: bağlantı ve durum sözleşmesi");
  await t("surface() shell.js'in haritasını kullanıyor (port gömülü değil)", () => {
    if (!/F1\.surfaceHref \|\| F1\.surfaceUrl/.test(src)) throw new Error("surface() shell.js'e bağlı değil");
  });
  await t("hash ile hafta sonu seçimi var", () => {
    if (!/location\.hash/.test(src)) throw new Error("hash okunmuyor");
    if (!/history\.replaceState/.test(src)) throw new Error("hash yazılmıyor");
  });
  await t("sprint varsayımı strip rozetinde bildiriliyor", () => {
    if (!/shapebadge/.test(src)) throw new Error("shapebadge yok");
    if (!/varsayımı/.test(src)) throw new Error("varsayım sözcüğü yok");
  });
  await t("LIVE rozeti prefers-reduced-motion'a saygılı", () => {
    if (!/prefers-reduced-motion/.test(src)) throw new Error("animasyon kapatılmıyor");
  });
  await t("mobilde kart ızgarası iki sütun", () => {
    const m = src.match(/@media \(max-width: 760px\)[\s\S]{0,900}?\.cards\s*\{[^}]*\}/);
    if (!m) throw new Error("mobil .cards kuralı yok");
    if (!/repeat\(2,/.test(m[0])) throw new Error("iki sütun değil: " + m[0]);
  });
  await t("dokunma hedefi >=44px", () => {
    const m = src.match(/\.scard\s*\{[^}]*min-height:\s*(\d+)px/);
    if (!m) throw new Error(".scard min-height yok");
    if (Number(m[1]) < 44) throw new Error("min-height " + m[1] + "px");
  });

  section("5. sunucu tarafı sözleşmesi (welcome_app.py)");
  const py = read("welcome_app.py");
  await t("PWA yüzeyi kayıtlı", () => {
    if (!/pwa\.install\(app, "welcome"\)/.test(py)) throw new Error("pwa.install yok");
  });
  await t("kart verisi weekend.overview'dan geliyor (ikinci hesap yok)", () => {
    if (!/weekend\.overview\(/.test(py)) throw new Error("overview çağrılmıyor");
    // The card grid must not build its own state labels either: `cards` comes
    // from overview() verbatim. session_state() is allowed in the LIST summary
    // (it needs a live flag per weekend), just not as a second card renderer.
    if (/_STATE_LABELS|headline/.test(py)) throw new Error("kart alanları sunucuda yeniden üretiliyor");
  });
  await t("kart etiketleri kısa biçimden geliyor (store.card_label)", () => {
    const wk = read("weekend.py");
    if (!/store\.card_label\(/.test(wk)) throw new Error("card_label kullanılmıyor");
  });
  await t("liste hafta sonu anahtarına göre tekilleştiriliyor", () => {
    if (!/seen\.get\(w\["key"\]\)/.test(py)) throw new Error("tekilleştirme yok");
  });

  console.log(bad === 0 ? "\n" + n + " TEST GEÇTİ" : "\n" + bad + "/" + n + " HATA");
  process.exit(bad ? 1 : 0);
})();
