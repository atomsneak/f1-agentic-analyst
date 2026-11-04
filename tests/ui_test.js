#!/usr/bin/env node
/* Yüzeylerin tarayıcı tarafı davranış testleri.
 *
 * Neden var: bu makinede başsız tarayıcı çalışmıyor (snap-confine izin
 * vermiyor, playwright'ın chromium'u da snap'e symlink). Yani "sayfayı aç,
 * bak" adımı otomatikleştirilemiyor. Bunun yerine sayfaların GERÇEK
 * kaynağını alıp küçük bir DOM taklidi üzerinde çalıştırıyoruz: mantık
 * test ediliyor, görünüm değil. Görsel doğrulama hâlâ elle yapılmalı.
 *
 * Sınanan şeyler, her biri geçmişte gerçekten kırılmış olduğu için:
 *
 * 1. Mobil panel aç/kapa (sheetSet) ve tutamak önizlemesi.
 * 2. Klavye yüksekliği telafisi (gerçek bir Android telefonda panel klavyenin
 *    arkasında kalıyordu).
 * 3. fetchJSON: HTML gövdesi hata mesajına SIZMAMALI (vekil 502'si
 *    sohbete `<!DOCTYPE html>` olarak düşüyordu).
 * 4. Kolon sırası: başlık ile hücre aynı sırada olmalı (GRID/GAP kaymıştı).
 *
 * Çalıştırma: node tests/ui_test.js   (run.sh test bunu kendisi çağırır)
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

/* Kaynaktan bir kod aralığı keser. Sayfanın tamamını çalıştırmak fetch,
 * EventSource, gerçek DOM ister; burada sadece test edilen birim alınıyor. */
function slice(source, from, to) {
  const i = source.indexOf(from);
  if (i < 0) throw new Error("başlangıç bulunamadı: " + from.slice(0, 50));
  const j = source.indexOf(to, i);
  if (j < 0) throw new Error("bitiş bulunamadı: " + to.slice(0, 50));
  return source.slice(i, j);
}

/* Asgari DOM öğesi. classList.toggle iki argümanlı hâliyle çalışır --
 * kodun kullandığı tek biçim bu. */
function mkEl(cls) {
  return {
    attrs: {}, textContent: "", style: {}, scrollTop: 0, scrollHeight: 800,
    classList: {
      _s: new Set(cls || []),
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(ev, fn) { (this._l ||= {})[ev] = fn; },
  };
}

const raf = (f) => f();

/* Sayfa kodunun ihtiyaç duyduğu shell.js yüzeyi. sheetPreview F1.plain
 * kullanıyor (şerit textContent ile yazıldığı için `**` ham görünmesin).
 *
 * shell.js açılışta PWA işlerini de yapıyor (rozet + worker kaydı). Bu taklit
 * DOM'da header yok: `querySelector` null dönünce ikisi de kendini kapatıyor,
 * worker kaydı da serviceWorker/isSecureContext yokluğunda hiç denenmiyor.
 * Yani buradaki stub GERÇEK bir sayfayı taklit etmiyor, sadece shell.js'in
 * çalışabilmesi için gereken en küçük yüzeyi veriyor. */
function shellF1() {
  const src = read("static/shell.js");
  const win = {};
  new Function("location", "document", "window", src)(
    { pathname: "/", protocol: "http:", hostname: "h" },
    /* `getElementById` is part of the surface now: shell.js's AI settings panel
     * looks up its dialog that way (an id lookup, not a selector). Without it
     * boot() threw and EVERY test in this file failed, which is exactly the
     * failure mode the comment above warns about -- the stub has to grow when
     * shell.js legitimately grows. Returning null makes aiPanel() bail out on
     * its first line, which is the real behaviour on a page with no dialog. */
    { readyState: "complete", querySelectorAll: () => [],
      querySelector: () => null, getElementById: () => null, addEventListener() {} },
    win);
  return win.F1;
}

// ------------------------------------------------- 1. mobil panel (canlı sayfa)

async function sheetLive() {
  section("1. canlı sayfa — mobil asistan paneli");
  const html = read("index.html");
  const src = slice(html, "const SHEET = {", "\nfunction restoreTranscript()");

  const els = { chat: mkEl([]), grip: mkEl(), gprev: mkEl(), msgs: mkEl() };
  // Başlangıç durumu gerçek markup'tan: aria-expanded orada statik yazılı.
  const sectionTag = html.match(/<section[^>]*id="chat"[^>]*>/)[0];
  els.chat.classList._s = new Set(
    ((sectionTag.match(/class="([^"]*)"/) || [, ""])[1]).split(/\s+/).filter(Boolean));
  const gripTag = html.match(/<button[^>]*id="grip"[^>]*>/)[0];
  els.grip.attrs["aria-expanded"] = (gripTag.match(/aria-expanded="([^"]*)"/) || [, "?"])[1];

  const $ = (s) => els[s.slice(1)] || null;
  const api = new Function("$", "requestAnimationFrame", "window", "document", "F1",
    src + "\nreturn {SHEET, sheetSet, sheetPreview};"
  )($, raf, { matchMedia: () => ({ matches: false }) }, {}, shellF1());

  await t("başlangıçta kapalı, aria-expanded=false", () => {
    if (!els.chat.classList.contains("collapsed")) throw new Error("kapalı değil");
    if (els.grip.attrs["aria-expanded"] !== "false") throw new Error("aria yanlış");
  });
  await t("sheetSet(true) açar, aria güncellenir", () => {
    api.sheetSet(true);
    if (els.chat.classList.contains("collapsed")) throw new Error("açılmadı");
    if (els.grip.attrs["aria-expanded"] !== "true") throw new Error("aria yanlış");
  });
  await t("sheetSet(false) kapatır", () => {
    api.sheetSet(false);
    if (!els.chat.classList.contains("collapsed")) throw new Error("kapanmadı");
    if (els.grip.attrs["aria-expanded"] !== "false") throw new Error("aria yanlış");
  });
  await t("açılınca mesaj listesi sona kayar", () => {
    els.msgs.scrollTop = 0;
    api.sheetSet(true);
    if (els.msgs.scrollTop !== els.msgs.scrollHeight) throw new Error("kaydırılmadı");
  });
  await t("önizleme tutamağa yazılır", () => {
    api.sheetPreview("AllClear, LEC 1:37.9 attı.");
    if (!els.gprev.textContent.startsWith("AllClear")) throw new Error("yazılmadı");
  });
  await t("önizleme tek satıra indirilir", () => {
    api.sheetPreview("birinci\n\nikinci   satır");
    if (/\n/.test(els.gprev.textContent)) throw new Error("satır sonu kaldı");
    // \s+ tek boşluğa iner: "birinci ikinci satır"
    if (els.gprev.textContent !== "birinci ikinci satır") {
      throw new Error("beklenmeyen: " + JSON.stringify(els.gprev.textContent));
    }
  });
  await t("önizleme 120 karakterde kesilir", () => {
    api.sheetPreview("x".repeat(400));
    if (els.gprev.textContent.length > 120) throw new Error("uzun: " + els.gprev.textContent.length);
  });
  await t("boş önizleme varsayılana döner", () => {
    api.sheetPreview("");
    if (!els.gprev.textContent.includes("soru sor")) throw new Error("varsayılan yok");
  });
}

// ---------------------------------------------- 2. mobil panel (arşiv sayfası)

async function sheetArchive() {
  section("2. arşiv sayfası — sohbet paneli");
  const html = read("archive.html");
  const src = slice(html, "const SHEET = {", "\nasync function ask(){");
  const els = { chatpanel: mkEl(["collapsed"]), grip: mkEl(), gprev: mkEl(), msgs: mkEl() };
  els.grip.attrs["aria-expanded"] = "false";
  const $ = (s) => els[s.slice(1)] || null;
  const api = new Function("$", "requestAnimationFrame", "window", "F1",
    src + "\nreturn {SHEET, sheetSet, sheetPreview};"
  )($, raf, { matchMedia: () => ({ matches: false }) }, shellF1());

  await t("başlangıçta kapalı", () => {
    if (!els.chatpanel.classList.contains("collapsed")) throw new Error("açık");
  });
  await t("aç/kapa çalışır", () => {
    api.sheetSet(true);
    if (els.chatpanel.classList.contains("collapsed")) throw new Error("açılmadı");
    api.sheetSet(false);
    if (!els.chatpanel.classList.contains("collapsed")) throw new Error("kapanmadı");
  });
  await t("önizleme yazılır, tek satır", () => {
    api.sheetPreview("VER 25.972 attı.\nHAM geride.");
    if (/\n/.test(els.gprev.textContent)) throw new Error("satır sonu");
  });
  await t("boş önizleme varsayılana döner", () => {
    api.sheetPreview("");
    if (!els.gprev.textContent.includes("seçili turla")) throw new Error("varsayılan yok");
  });
}

// --------------------------------------------------------- 3. klavye telafisi

async function keyboard() {
  section("3. klavye telafisi (Android)");
  const html = read("index.html");
  const src = slice(html, "function bindKeyboard(){", "\n(function bindGripGesture");

  function run(vvHeight, innerHeight, isMobile, screen) {
    const panel = { id: screen || "chat", style: {} };
    const window = {
      innerHeight,
      visualViewport: vvHeight == null ? null : {
        height: vvHeight, offsetTop: 0, addEventListener: () => {},
      },
      matchMedia: () => ({ matches: isMobile }),
    };
    new Function("$", "window", src + "\nbindKeyboard();")(
      (s) => (s === "#" + panel.id ? panel : null), window);
    return panel;
  }

  await t("masaüstünde bottom'a dokunmaz", () => {
    const p = run(900, 1200, false);
    if (p.style.bottom) throw new Error("dokundu: " + p.style.bottom);
  });
  await t("klavye kapalıyken boş", () => {
    const p = run(800, 820, true);
    if (p.style.bottom) throw new Error("boş değil: " + p.style.bottom);
  });
  await t("klavye açılınca panel yukarı itilir", () => {
    // Ölçülen bir Android telefonda: innerHeight ~873, klavye açık ~508 -> ~365px
    const p = run(508, 873, true);
    const b = parseInt(p.style.bottom, 10);
    if (!(b >= 300 && b <= 400)) throw new Error("beklenmeyen: " + p.style.bottom);
    console.log("       -> bottom = " + p.style.bottom + " (~365px klavye)");
  });
  await t("adres çubuğu dalgalanması klavye sanılmaz (<80px)", () => {
    const p = run(873 - 60, 873, true);
    if (p.style.bottom) throw new Error("yanlış tetikledi: " + p.style.bottom);
  });
  await t("visualViewport yoksa çökmez", () => {
    const p = run(null, 800, true);
    if (p.style.bottom) throw new Error("değişti");
  });
}

// --------------------------------------------------------------- 4. fetchJSON

async function fetchJson() {
  section("4. fetchJSON — HTML gövdesi mesaja sızmaz");
  const src = read("static/shell.js");
  const CF = '\n<!DOCTYPE html>\n<html><body>502 Bad Gateway</body></html>';

  function load(fetchImpl) {
    const win = {};
    new Function("location", "document", "window", "fetch", src)(
      { pathname: "/", protocol: "http:", hostname: "h" },
      { readyState: "complete", querySelectorAll: () => [],
        querySelector: () => null, getElementById: () => null, addEventListener() {} },
      win, fetchImpl);
    return win.F1;
  }
  const resp = (body, { ok = true, status = 200, statusText = "OK" } = {}) => Promise.resolve({
    ok, status, statusText,
    json: () => Promise.resolve(JSON.parse(body)),
    text: () => Promise.resolve(body),
  });

  await t("başarılı JSON döner", async () => {
    const d = await load(() => resp('{"a":1}')).fetchJSON("api/x");
    if (d.a !== 1) throw new Error("yanlış gövde");
  });
  await t("502 HTML -> mesajda HTML yok", async () => {
    let msg = "";
    try { await load(() => resp(CF, { ok: false, status: 502, statusText: "Bad Gateway" })).fetchJSON("api/x"); }
    catch (e) { msg = e.message; }
    if (!msg.includes("502")) throw new Error("kod yok: " + msg);
    if (/<!DOCTYPE|<html/i.test(msg)) throw new Error("HTML sızdı: " + msg);
    if (!msg.includes("HTML")) throw new Error("açıklama yok: " + msg);
    console.log("       -> " + msg);
  });
  await t("hata kodu .status alanında", async () => {
    try {
      await load(() => resp("nope", { ok: false, status: 404, statusText: "Not Found" })).fetchJSON("api/x");
      throw new Error("hata atılmadı");
    } catch (e) { if (e.status !== 404) throw new Error("status yok: " + e.status); }
  });
  await t("200 ama JSON değil -> anlaşılır mesaj", async () => {
    let msg = "";
    try { await load(() => resp(CF, { ok: true, status: 200 })).fetchJSON("api/x"); }
    catch (e) { msg = e.message; }
    if (/<!DOCTYPE/i.test(msg)) throw new Error("HTML sızdı: " + msg);
  });
  await t("düz metin hata gövdesi korunur", async () => {
    let msg = "";
    try { await load(() => resp("vekil: kapalı", { ok: false, status: 502, statusText: "BG" })).fetchJSON("api/x"); }
    catch (e) { msg = e.message; }
    if (!msg.includes("vekil")) throw new Error("gövde kayboldu: " + msg);
  });

  // --- markdown biçimlendirici ---
  const F1 = load(() => resp("{}"));

  await t("fmt **kalın**ı <b> yapar", () => {
    if (F1.fmt("**BEA** yarış dışı") !== "<b>BEA</b> yarış dışı") {
      throw new Error(JSON.stringify(F1.fmt("**BEA** yarış dışı")));
    }
  });
  await t("fmt `kod`u <code> yapar", () => {
    if (F1.fmt("süre `1:38.993`") !== "süre <code>1:38.993</code>") {
      throw new Error(JSON.stringify(F1.fmt("süre `1:38.993`")));
    }
  });
  await t("fmt önce kaçırır (XSS)", () => {
    const out = F1.fmt('<img src=x onerror=alert(1)>**B**');
    if (/<img/i.test(out)) throw new Error("ham HTML geçti: " + out);
    if (!out.includes("<b>B</b>")) throw new Error("kalın kayboldu: " + out);
  });
  /* ## başlık: gerçek arşiv kaydında cevap "## Bu yarış henüz bitmedi" diye
   başladı ve panele ÇİĞ hashlarla düştü -- model Markdown başlığı yazıyor ve
   biçimlendirici yalnız ** ile ` ` biliyordu. */
  await t("fmt ## başlığı <b> yapar", () => {
    const out = F1.fmt("## Bu yarış henüz bitmedi");
    if (out !== "<b>Bu yarış henüz bitmedi</b>") throw new Error(JSON.stringify(out));
  });
  await t("fmt satır başındaki - ve * maddelerini madde işaretine çevirir", () => {
    const out = F1.fmt("Durum:\n- ANT lider\n* VER +2.1");
    if (!/\u2022 ANT lider/.test(out) || !/\u2022 VER/.test(out)) {
      throw new Error(JSON.stringify(out));
    }
  });
  await t("fmt cümle içindeki # ve - işaretlerine dokunmaz", () => {
    const src = "C#7 numaralı araç, skor 3 - 2";
    if (F1.fmt(src) !== src) throw new Error(JSON.stringify(F1.fmt(src)));
  });
  await t("fmt başlık ararken HTML kaçırmasını bozmuyor (XSS)", () => {
    const out = F1.fmt("## <img src=x onerror=alert(1)>");
    if (/<img/i.test(out)) throw new Error("ham HTML geçti: " + out);
    if (!/^<b>/.test(out)) throw new Error("başlık kayboldu: " + out);
  });
  await t("plain başlık işaretlerini siler (şerit fmt ile aynı)", () => {
    if (F1.plain("## Başlık") !== "Başlık") throw new Error(JSON.stringify(F1.plain("## Başlık")));
  });
  await t("plain **işaretlerini siler (şerit textContent)", () => {
    if (F1.plain("**BEA** ve `COL`") !== "BEA ve COL") {
      throw new Error(JSON.stringify(F1.plain("**BEA** ve `COL`")));
    }
  });
}

// ------------------------------------------------ 5. nav linkleri (iki erişim)

async function nav() {
  section("5. shell.js — nav iki erişim şeklinde de doğru");
  const src = read("static/shell.js");

  function run(pathname, protocol, hostname) {
    const links = ["8103", "8099", "8100", "8101"].map((p) => ({
      _p: p, href: "http://127.0.0.1:" + p + "/",
      getAttribute(k) { return k === "data-port" ? this._p : null; },
    }));
    const win = {};
    new Function("location", "document", "window", src)(
      { pathname, protocol, hostname },
      { readyState: "complete", querySelector: () => null,
        querySelectorAll: (s) => (s === "a[data-port]" ? links : []),
        getElementById: () => null, addEventListener() {} },
      win);
    return links.map((l) => l.href);
  }

  await t("porttan açıldığında host:port", () => {
    const h = run("/", "http:", "192.0.2.10");
    if (!h[1].includes("192.0.2.10:8099")) throw new Error(h[1]);
    if (!h[0].includes("192.0.2.10:8103")) throw new Error(h[0]);
  });
}

// ------------------------------------------------------- 6. kolon sırası

async function columns() {
  section("6. canlı tablo — başlık ve hücre sırası");
  const html = read("index.html");
  /* Comments are stripped first: the header now carries an HTML comment
   * explaining the sector columns, and `[^<]*` would stop dead at it and count
   * one header fewer than there are columns. The template's comments are
   * stripped for the same reason. */
  const strip = (t) => t.replace(/<!--[\s\S]*?-->/g, "");
  const heads = [...strip(html).matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);
  let tpl = strip(html.match(/return `<tr>([\s\S]*?)<\/tr>`/)[1]);
  /* `${secCell(...)}` expands to exactly one <td> at runtime, so the count is
   * only right if the test expands it the same way. Substituting a stand-in
   * marker keeps the assertion meaningful ("this column renders this row field")
   * without pretending the template writes raw <td> tags there. */
  /* `${secCells(r, useBest)}` expands to THREE <td> at runtime. The test expands
   * it the same way instead of pretending the template writes raw tags. */
  tpl = tpl.replace(/\$\{secCells\(([^)]*)\)\}/g,
    '<td class="sec">secCell(r.s1)</td><td class="sec">secCell(r.s2)</td>'
    + '<td class="sec">secCell(r.s3)</td>');
  tpl = tpl.replace(/\$\{secCell\(([^)]*)\)\}/g, '<td class="sec">secCell($1)</td>');
  const cells = [...tpl.matchAll(/<td([^>]*)>/g)];
  const td = [...tpl.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => m[1]);

  await t("başlık ve hücre sayısı eşit", () => {
    if (heads.length !== cells.length) throw new Error(heads.length + " vs " + cells.length);
  });
  /* TEAM sütunu kaldırıldı (takım rengi sürücü çubuğunda), yani indeksler bir
   * kaydı: GRID artık 3., GAP 4. Sıra iddiası hâlâ aynı şeyi sınıyor -- başlık
   * ile altındaki hücre aynı alanı göstermeli. */
  await t("GRID başlığının altında grid değeri var", () => {
    if (!/r\.grid/.test(td[2])) throw new Error("GRID hücresi: " + td[2].trim().slice(0, 40));
  });
  await t("GAP başlığının altında gap değeri var", () => {
    if (!/r\.gap/.test(td[3])) throw new Error("GAP hücresi: " + td[3].trim().slice(0, 40));
  });
  await t("TEAM kolonu kaldırıldı (hem başlıkta hem hücrede)", () => {
    if (heads.some((h) => /^TEAM$/i.test(h))) throw new Error("başlıkta TEAM var");
    if (/class="team"/.test(tpl)) throw new Error("hücrede team var");
  });
  await t("ceza rozeti sürücü hücresinin içinde", () => {
    if (!/penaltyBadge\(r\)/.test(tpl)) throw new Error("rozet çağrılmıyor");
  });
  /* Sektör kolonları eklendi: "nerede zaman kaybetti" tablodan okunabilsin.
     Sıra başlıktaki sırayla aynı olmalı, yoksa bir sürücünün S1'i başka bir
     sütunun altında görünür -- bu projede GRID/GAP için tam olarak bu olmuştu. */
  await t("sektör kolonları S1,S2,S3,IDEAL,LOSS sırasında", () => {
    const idx = heads.map((h, i) => [h.trim(), i]).filter(([h]) => /^S[123]$|^IDEAL$|^LOSS$/.test(h));
    const names = idx.map(([h]) => h).join(",");
    if (names !== "S1,S2,S3,IDEAL,LOSS") throw new Error("başlıklar: " + names);
    const first = idx[0][1];
    if (!/secCell\(r\.s1/.test(td[first])) throw new Error("S1 hücresi: " + td[first].trim().slice(0, 40));
    // The cell builder must be the one deciding colours, not the row template.
    if (!/function secCells\(/.test(html)) throw new Error("secCells yok");
    if (!/r\.loss/.test(td[idx[4][1]])) throw new Error("LOSS hücresi: " + td[idx[4][1]].trim().slice(0, 40));
  });
  await t("sektör hücresi renk sınıfını SUNUCUDAN gelen s_status'tan alıyor", () => {
    const src = html.match(/function secCell\([\s\S]*?\n\}/);
    if (!src) throw new Error("secCell yok");
    if (!/s-\" \+ status\[i\]/.test(src[0])) throw new Error("status kullanılmıyor: " + src[0].slice(0, 160));
    // A second, browser-side comparison is what makes two surfaces disagree.
    if (/\.toFixed\(3\).*<.*s-p/.test(src[0])) throw new Error("renk yeniden hesaplanıyor");
  });
  /* Mini sektör şeritleri. Üç şeyi sınıyoruz, üçü de sessiz bozulma üretir:
   *   - kod->renk eşlemesi kaynağın GERÇEK anlamı olmalı (2049 mor, 2048 yeşil);
   *   - kod 0 "veri yok" demek, boyanmamalı (gri çubuk ölçüm varmış gibi okunur);
   *   - şerit sektörün kendi turundan gelmeli: en iyi tur seçiliyken b_segs,
   *     yoksa son turun segs'i. Karışırsa iki farklı tur tek hücrede birleşir. */
  await t("mini sektör şeridi kod eşlemesi kaynakla aynı", () => {
    const m = html.match(/const SEG_CLS = \{([^}]*)\}/);
    if (!m) throw new Error("SEG_CLS yok");
    const map = {};
    for (const pair of m[1].split(",")) {
      const [k, v] = pair.split(":").map((s) => s.trim().replace(/["']/g, ""));
      if (k) map[k] = v;
    }
    if (map["2049"] !== "mp") throw new Error("2049 (seans en iyisi) mor olmalı: " + map["2049"]);
    if (map["2048"] !== "mg") throw new Error("2048 (kişisel en iyi) yeşil olmalı: " + map["2048"]);
    if (map["2051"] !== "my") throw new Error("2051 (gelişme yok) sarı olmalı: " + map["2051"]);
    if (map["0"] !== "m0") throw new Error("0 'veri yok' ayrı sınıf olmalı: " + map["0"]);
  });
  await t("mini şerit renkleri CSS'te tanımlı, 0 şeffaf", () => {
    for (const cls of ["mp", "mg", "my", "m0"]) {
      if (!new RegExp("\\.mini \\." + cls + "\\{").test(html))
        throw new Error(".mini ." + cls + " CSS'te yok");
    }
    if (!/\.mini \.m0\{background:transparent\}/.test(html))
      throw new Error("kod 0 boyanıyor: olmayan ölçüm varmış gibi görünür");
  });
  await t("mini şerit gösterilen turun kodlarından geliyor", () => {
    const src = html.match(/function secCells\([\s\S]*?\n\}/);
    if (!src) throw new Error("secCells yok");
    if (!/secCell\(r\.bs1[\s\S]*?r\.b_segs/.test(src[0]))
      throw new Error("en iyi tur sektörleri b_segs ile eşleşmiyor");
    if (!/secCell\(r\.s1[\s\S]*?r\.segs/.test(src[0]))
      throw new Error("son tur sektörleri segs ile eşleşmiyor");
  });
  await t("kodsuz mini şerit hiç çizilmiyor", () => {
    const src = html.match(/function miniBars\([\s\S]*?\n\}/);
    if (!src) throw new Error("miniBars yok");
    // Boş bir şerit "hepsi gelişme yok" gibi okunurdu.
    if (!/if\(raw == null \|\| raw === ""\) return "";/.test(src[0]))
      throw new Error("boş kod şerit çiziyor gibi: " + src[0].slice(0, 160));
  });
}

// ------------------------------------------------ 7. panel CSS sağlığı

/* `min-height`, `max-height`'ı YENER. Panel ilk yazıldığında `#chat` için
 * `min-height:58dvh` vardı (sohbet akıştaki kolonken konmuştu) ve
 * `.collapsed{max-height:46px}` hiç uygulanmadı: panel telefonda kapanmıyordu
 * ve açık görünüyordu. Tarayıcı olmadan bu tür bir öncelik hatası görünmez --
 * bu yüzden kural metni üzerinden sınıyoruz. */
async function panelCss() {
  section("7. panel CSS — kapanabilirlik garantisi");

  for (const [file, sel, block] of [
    ["index.html", "#chat", "max-width:640px"],
    ["archive.html", "#chatpanel", "max-width: 640px"],
  ]) {
    const html = read(file);
    const css = html.match(/<style>([\s\S]*?)<\/style>/)[1]
      .replace(/\/\*[\s\S]*?\*\//g, "");
    // Sadece telefon bloğunu al: panel kuralları orada.
    const at = css.indexOf(block);
    const mobile = at >= 0 ? css.slice(at) : css;

    await t(`${file}: .collapsed yüksekliği AÇIKÇA kilitler`, () => {
      const m = mobile.match(new RegExp("\\" + sel + "\\.collapsed\\s*\\{([^}]*)\\}"));
      if (!m) throw new Error("collapsed kuralı yok");
      if (!/(^|;)\s*height\s*:/.test(m[1])) {
        throw new Error("height yok, sadece max-height: " + m[1].trim());
      }
    });

    await t(`${file}: panelde sıfır olmayan min-height yok`, () => {
      const re = new RegExp("\\" + sel + "\\b[^{}]*\\{([^}]*)\\}", "g");
      let m, bad = [];
      while ((m = re.exec(mobile))) {
        const mn = m[1].match(/min-height\s*:\s*([^;}]+)/);
        if (mn && !/^(0|0px|auto)$/.test(mn[1].trim())) bad.push(mn[1].trim());
      }
      if (bad.length) throw new Error("min-height max-height'ı ezer: " + bad.join(", "));
    });
  }

  await t("grip flex-shrink etmiyor (46px korunur)", () => {
    for (const f of ["index.html", "archive.html"]) {
      const css = read(f).match(/<style>([\s\S]*?)<\/style>/)[1]
        .replace(/\/\*[\s\S]*?\*\//g, "");
      // Birden çok #grip kuralı var (masaüstünde display:none, telefonda
      // görünür). flex kuralını taşıyanı arıyoruz.
      const all = [...css.matchAll(/#grip\s*\{([^}]*)\}/g)].map((m) => m[1]);
      if (!all.some((b) => /flex\s*:\s*0\s+0\s+46px/.test(b))) {
        throw new Error(f + ": flex:0 0 46px yok, tutamaç ezilir");
      }
    }
  });

  await t("açık panel ekranın en fazla %70'i", () => {
    // %78'de tablo tamamen örtülüyordu (ölçüldü 713/915). Telefonun işi
    // tabloyu gizlemek değil, yanında durmak.
    for (const f of ["index.html", "archive.html"]) {
      const css = read(f).match(/<style>([\s\S]*?)<\/style>/)[1];
      // Panelin kendi yüksekliği: ".collapsed" OLMAYAN #chat/#chatpanel kuralı.
      const m = css.match(/(?:#chat|#chatpanel)\s*\{[^}]*height\s*:\s*(\d+)dvh/);
      if (!m) throw new Error(f + ": panel dvh yüksekliği yok");
      if (Number(m[1]) > 70) throw new Error(f + ": panel çok yüksek: " + m[1] + "dvh");
    }
  });

  await t("theme.css [hidden] kuralı var (display ezilmesin)", () => {
    const css = read("static/theme.css");
    if (!/\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/.test(css)) {
      throw new Error("[hidden] kuralı yok: .subtabs{display:flex} onu ezer");
    }
  });

  await t("arşiv: panel içinde msgs max-height kilidi kaldırılmış", () => {
    const html = read("archive.html");
    const css = html.match(/<style>([\s\S]*?)<\/style>/)[1];
    if (!/#chatpanel #msgs\{max-height:none\}/.test(css)) {
      throw new Error("900px bloğundan gelen 46dvh kilidi panelde geçerli kalır");
    }
  });
}


// ------------------------------------------- 8. iki sohbet panelinin hizası

/* Kullanıcı isteği: "hem webde hem mobilde chat boxlar ve chat box altı
 * bilgilendirme alanları paralel/align olsun". Hizalama CSS ölçüsüdür,
 * DOM taklidinde görünmez -- bu yüzden yapı sırasını ve sınıf adlarını
 * sınıyoruz; gerçek piksel ölçümü layout_test.js'te. */
// ------------------------------------------------ 9. arşiv derin bağlantısı
/* Hafta sonu ızgarasındaki bir kart arşivi `?rid=<id>` ile açıyor. Bu bağlantı
 * okunmazsa kart yanlış seansı gösterir -- ve yanlış seansı "istenen seans" gibi
 * göstermek, bu arşivin baştan beri kaçındığı hata. Test, boot()'un seçimi
 * nasıl yaptığını gerçek kaynaktan okuyup iki durumda çalıştırıyor. */
async function deepLink() {
  section("9. arşiv ?rid= derin bağlantısı");
  const src = read("archive.html");

  const pickSrc = slice(src, "  const want = Number(new URLSearchParams",
                        "  await load(pick);");

  /* `sel` is the real <select> the code writes into; the stub only records what
   * it was given, so the assertions are about the CHOSEN id, not the DOM. */
  function pick(ids, search) {
    const body = pickSrc.replace(/await load\(pick\);/, "");
    const sel = { value: null };
    const fn = new Function("location", "ids", "sel", body + "\nreturn pick;");
    return fn({ search }, ids, sel);
  }

  await t("?rid= geçerliyse o yarış seçiliyor", () => {
    const got = pick([23, 22, 19], "?rid=19");
    if (got !== 19) throw new Error("seçilen: " + got);
  });
  await t("?rid= yoksa en yeni yarış", () => {
    const got = pick([23, 22, 19], "");
    if (got !== 23) throw new Error("seçilen: " + got, " — Number(null) 0 olur, ids[0] beklenir");
  });
  await t("?rid= arşivde yoksa en yeni yarışa düşüyor (404 yok)", () => {
    const got = pick([23, 22, 19], "?rid=999");
    if (got !== 23) throw new Error("seçilen: " + got);
  });
  await t("bozuk ?rid= çökmüyor", () => {
    for (const bad of ["?rid=abc", "?rid=", "?rid=-1", "?rid=1e999"]) {
      const got = pick([23, 22, 19], bad);
      if (![23, 22, 19].includes(got)) throw new Error(bad + " -> " + got);
    }
  });
}

// ------------------------------------------------ 10. AI sağlayıcı arayüzü
/* İkinci bir kullanıcı kendi sağlayıcısını seçebilmeli. Sınanan şey mantık:
 * anahtar tarayıcıya geri gelmemeli (input asla önceden doldurulmaz), boş
 * anahtar kayıtlıyı silmemeli (null gönderilmeli, "" değil), ve iki sohbet
 * yüzeyinde de aynı diyalog bulunmalı. */
// ------------------------------------------------ 11. arşiv seans tipi
/* Arşiv sayfası yarış için yazılmıştı ve HER seansı yarış gibi sunuyordu.
 * Sıralamada iki yanlış iddia üretiyordu: "Lastik stratejisi" (sıralamada
 * strateji yok) ve "tur N/M" (tur sayacı saat tikidir). Sınanan şey, sayfanın
 * bu iddiaları seans tipine göre düzeltmesi. */
// ------------------------------------------------ 12. sektör renk özgüllüğü
/* CSS özgüllük hatası: renkler sunucudan doğru geliyordu ama ekranda gri
 * görünüyordu. Sebep `td.sec.part` kuralının `td.sec.s-y` ile AYNI özgüllükte
 * (0,2,1) olması ve sayfada SONRA gelmesiydi -- yani her rengi eziyordu.
 * Tarayıcıda ölçüldü: sınıf "sec s-y part", hesaplanan renk rgb(139,152,169)
 * (gri), olması gereken rgb(216,178,60) (sarı).
 *
 * Bu test kural metnini sınıyor çünkü olay tamamen CSS sırası/özgüllüğünde:
 * tarayıcısız yakalanabilen tek biçim bu. */
async function sectorCss() {
  section("12. sektör renkleri — CSS özgüllüğü");
  const html = read("index.html");
  const css = html.match(/<style>([\s\S]*?)<\/style>/)[1].replace(/\/\*[\s\S]*?\*\//g, "");

  await t("renk kuralları .part'tan daha güçlü özgüllükte", () => {
    // The colour rules must beat the neutral `.part` rule; if they are written
    // with equal specificity and appear earlier, they lose silently.
    const colourRules = css.match(/td\.sec\[class\*="s-"\]\.s-[pgy]\s*\{[^}]*color:/g) || [];
    if (colourRules.length < 3) {
      throw new Error("renk kuralları özgüllük artırılmamış: " + colourRules.length);
    }
  });
  await t("üç renk de tanımlı", () => {
    for (const [cls, expect] of [["s-p", "c9a6ff"], ["s-g", "2ecc71"], ["s-y", "d8b23c"]]) {
      const re = new RegExp('td\\.sec\\[class\\*="s-"\\]\\.' + cls + '\\s*\\{[^}]*color:\\s*#' + expect);
      if (!re.test(css)) throw new Error(cls + " rengi bulunamadı");
    }
  });
  await t("arşivde de aynı üç renk var", () => {
    const arch = read("archive.html");
    for (const c of ["s-p", "s-g", "s-y"]) {
      if (!new RegExp("td\\.sec\\." + c).test(arch)) throw new Error("arşivde " + c + " yok");
    }
  });
}

async function sessionTypeUi() {
  section("11. arşiv seans tipi uyarlaması");
  const html = read("archive.html");

  await t("applySessionType tanımlı ve yüklenirken çağrılıyor", () => {
    if (!/function applySessionType\(/.test(html)) throw new Error("fonksiyon yok");
    if (!/applySessionType\(d\.session_type/.test(html)) throw new Error("çağrılmıyor");
  });
  await t("sunucu session_type gönderiyor", () => {
    const py = read("archive.py");
    if (!/"session_type": stype/.test(py)) throw new Error("payload'da yok");
  });
  await t("yarışta başlık 'Lastik stratejisi', diğerlerinde 'Lastik kullanımı'", () => {
    if (!/isRace \? "Lastik stratejisi" : "Lastik kullanımı"/.test(html)) {
      throw new Error("başlık dallanmıyor");
    }
  });
  await t("yarış dışında tur etiketi 'Kayıt' oluyor", () => {
    if (!/lab\.textContent = isRace \? "Tur" : "Kayıt"/.test(html)) {
      throw new Error("tur etiketi dallanmıyor");
    }
  });
  await t("sıralama notu bölümleri ve 'strateji değil' ifadesini taşıyor", () => {
    if (!/strateji değil, lastik kullanımıdır/.test(html)) throw new Error("not yok");
    if (!/t\.segments/.test(html)) throw new Error("segmentler yazılmıyor");
  });
  await t("antrenman notu yarış mesafesi olmadığını söylüyor", () => {
    if (!/Antrenman seansı: yarış mesafesi, grid ve puan yok/.test(html)) {
      throw new Error("antrenman notu yok");
    }
  });
}

async function aiPanelTests() {
  section("10. AI sağlayıcı/model diyalogu");
  const shell = read("static/shell.js");
  const live = read("index.html");
  const arch = read("archive.html");

  await t("diyalog ve düğme her iki sohbet yüzeyinde de var", () => {
    for (const [name, html] of [["index.html", live], ["archive.html", arch]]) {
      if (!/id="aidlg"/.test(html)) throw new Error(name + ": dialog yok");
      if (!/id="aibtn"/.test(html)) throw new Error(name + ": düğme yok");
      for (const id of ["ailabel", "aibase", "aimodel", "aikey", "aitest", "aisave", "aidel"]) {
        if (!new RegExp('id="' + id + '"').test(html)) throw new Error(name + ": #" + id + " yok");
      }
    }
  });

  await t("anahtar alanı asla önceden doldurulmaz", () => {
    // A prefilled password input means the secret is in the DOM, readable by any
    // script on the page and visible to anything that snapshots the DOM.
    if (!/el\("aikey"\)\.value = ""/.test(shell)) {
      throw new Error("aikey sıfırlanmıyor");
    }
    if (/aikey"[^>]*value="/.test(live) || /aikey"[^>]*value="/.test(arch)) {
      throw new Error("input'ta value özniteliği var");
    }
  });

  await t('boş anahtar null olarak gönderiliyor ("" değil)', () => {
    // python api_key.strip() or None -> "" would be falsy too, but the JS contract
    // is explicit: null means "keep the stored key".
    if (!/api_key: el\("aikey"\)\.value\.trim\(\) \|\| null/.test(shell)) {
      throw new Error("api_key \"\" gönderiyor olabilir");
    }
  });

  await t("kaydetme ve test uçları ayrı", () => {
    if (!/fetchJSON\("api\/ai"/.test(shell)) throw new Error("kaydetme ucu yok");
    if (!/fetchJSON\("api\/ai\/test"/.test(shell)) throw new Error("test ucu yok");
  });

  await t("silme isteği pid taşıyor", () => {
    if (!/api\/ai\?pid=/.test(shell)) throw new Error("silme ucu yanlış");
  });

  await t("anahtarın tarayıcıya dönmediği metinde yazılı", () => {
    if (!/anahtar\s+sunucuda saklanır/i.test(live)) throw new Error("uyarı yok (canlı)");
    if (!/anahtar\s+sunucuda saklanır/i.test(arch)) throw new Error("uyarı yok (arşiv)");
  });

  await t("sunucu tarafı anahtarı geri döndürmüyor", () => {
    const py = read("ai_config.py");
    if (!/def _row_public/.test(py)) throw new Error("_row_public yok");
    const fn = py.match(/def _row_public[\s\S]*?\n\n\n/)[0];
    if (/"api_key"\s*:/.test(fn)) throw new Error("public çıktıda api_key var");
    if (!/has_key/.test(fn)) throw new Error("has_key yok");
  });
}

async function chatParity() {
  section("8. sohbet panelleri — yapı ve etiket paritesi");
  const live = read("index.html");
  const arch = read("archive.html");

  const order = (html, names) => {
    const idx = names.map(n => html.indexOf(n));
    return idx.every(i => i >= 0) && idx.every((v, i) => i === 0 || v > idx[i - 1]);
  };

  await t("canlı: head -> msgs -> chips -> usage -> form -> srcline sırası", () => {
    if (!order(live, ['id="chathead"', 'id="msgs"', 'id="chips"',
                      'id="usagebar"', 'id="f"', 'id="srcline"'])) {
      throw new Error("beklenen sıra yok");
    }
  });
  await t("arşiv: head -> msgs -> chips -> usage -> form -> srcline sırası", () => {
    if (!order(arch, ['class="chathead"', 'id="msgs"', 'id="chips"',
                      'id="usagebar"', 'id="ask"', 'id="srcline"'])) {
      throw new Error("beklenen sıra yok");
    }
  });
  await t("iki sayfada da #srcline var", () => {
    if (!live.includes('id="srcline"')) throw new Error("canlıda yok");
    if (!arch.includes('id="srcline"')) throw new Error("arşivde yok");
  });
  await t("iki sayfada da aynı #srcline ölçüsü", () => {
    const re = /#srcline\{([^}]*)\}/;
    const a = live.match(re), b = arch.match(re);
    if (!a || !b) throw new Error("kural eksik");
    const norm = x => x[1].replace(/\s+/g, "");
    if (norm(a) !== norm(b)) throw new Error("ölçüler farklı:\n" + a[1] + "\n" + b[1]);
  });
  await t("arşiv kaynak etiketi 'arşiv kaydı' diyor, çıplak 'kaynak live' değil", () => {
    if (!arch.includes("arşiv kaydı (")) throw new Error("etiket yok");
    // Eski hatalı kalıp geri gelmesin.
    if (/kaynak \$\{snap\.source/.test(arch)) throw new Error("eski 'kaynak live' kalıbı geri gelmiş");
  });
  await t("arşiv formu <form>, sticky değil", () => {
    if (!/<form id="ask">/.test(arch)) throw new Error("form değil");
    if (/#chatpanel #ask\{[^}]*position\s*:\s*sticky/.test(arch)) {
      throw new Error("sticky geri gelmiş: kapalı panelde form görünür olur");
    }
  });
  await t("arşivde sohbet çipi .chip değil .schip (sürücü pilleri korunur)", () => {
    if (!arch.includes('class="schip"') && !arch.includes("schip\">")) {
      // şablon string içinde geçiyor
      if (!/class="schip"/.test(arch)) throw new Error("schip yok");
    }
    // #gapchips hâlâ .chip kullanıyor olmalı
    if (!/className = "chip"/.test(arch)) throw new Error("sürücü pilleri bozulmuş");
  });
}

// ------------------------------------------------------------------- bitiş

(async () => {
  await sheetLive();
  await sheetArchive();
  await keyboard();
  await fetchJson();
  await nav();
  await columns();
  await panelCss();
  await chatParity();
  await deepLink();
  await aiPanelTests();
  await sessionTypeUi();
  await sectorCss();
  console.log(bad === 0 ? "\n" + n + " TEST GEÇTİ" : "\n" + bad + "/" + n + " HATA");
  process.exit(bad ? 1 : 0);
})();
