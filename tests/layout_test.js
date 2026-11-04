#!/usr/bin/env node
/* Görsel/düzen doğrulaması — GERÇEK tarayıcıda, gerçek ölçümle.
 *
 * Neden gerekli: ui_test.js mantığı sınıyor ama CSS önceliği, flex ezilmesi,
 * transform yüzdesi gibi hatalar orada görünmüyor. Bu turda üç hata tam da
 * böyle kaçmıştı:
 *   - panel 46px yerine 12px tutamak (flex-shrink)
 *   - panel hiç kapanmıyor (min-height > max-height)
 *   - [hidden] öğe görünür kalıyor (.subtabs display:flex eziyor)
 * Hepsi ancak ölçümle yakalanır.
 *
 * Tarayıcı: Chrome for Testing'in chrome-headless-shell'i. Bu makinede
 * /snap/bin/chromium ve firefox snap-confine yüzünden çalışmıyor; bu ikili
 * snap'e bağlı değil. Kurulum (bir kez):
 *   mkdir -p ~/.local/share/f1-agentic-analyst/tools
 *   cd ~/.local/share/f1-agentic-analyst/tools
 *   curl -sLO https://storage.googleapis.com/chrome-for-testing-public/\
 *131.0.6778.85/linux64/chrome-headless-shell-linux64.zip
 *   unzip chrome-headless-shell-linux64.zip
 *
 * Çalıştırma: node tests/layout_test.js [--shots /tmp/shots]
 * Servis ayakta olmalı (f1-live + f1-archive). Yoksa testler atlanır.
 */
"use strict";

const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SHOT = path.join(ROOT, "tools", "shot.js");
const BIN = path.join(os.homedir(),
  ".local/share/f1-agentic-analyst/tools/chrome-headless-shell-linux64/chrome-headless-shell");

const shotsIdx = process.argv.indexOf("--shots");
const SHOTDIR = shotsIdx > 0 ? process.argv[shotsIdx + 1] : null;

let bad = 0, n = 0, skipped = 0;
function t(name, fn) {
  n++;
  try { fn(); console.log("  ok   " + name); }
  catch (e) { bad++; console.log("  HATA " + name + ": " + e.message); }
}

/* Sayfayı açar, verilen JS'i çalıştırır, sonucu döndürür. */
function probe(url, js, { click, width = 412, height = 915, settle = 2800 } = {}) {
  const args = [SHOT, url, "/tmp/_probe.png", String(width), String(height), String(settle),
    "--eval", js];
  if (click) args.push("--click", click);
  const out = execFileSync(process.execPath, args, { encoding: "utf8", timeout: 90000 });
  const m = out.match(/eval -> (.*)/);
  if (!m) throw new Error("ölçüm alınamadı: " + out.trim().slice(0, 200));
  return JSON.parse(m[1]);
}

function shot(url, file, opts) {
  if (!SHOTDIR) return;
  const o = opts || {};
  const args = [SHOT, url, path.join(SHOTDIR, file),
    String(o.width || 412), String(o.height || 915), "3000"];
  if (o.click) args.push("--click", o.click);
  if (o.full) args.push("--full");
  execFileSync(process.execPath, args, { encoding: "utf8", timeout: 90000 });
  console.log("       ekran görüntüsü: " + path.join(SHOTDIR, file));
}

// ---------------------------------------------------------------- hazırlık

if (!fs.existsSync(BIN)) {
  console.log("\nchrome-headless-shell yok, görsel testler atlandı.");
  console.log("Kurulum için bu dosyanın başındaki komutlara bak.");
  process.exit(0);
}

/* Each surface has its own port and root path. Measuring the wrong page here
 * would silently pass -- the panel selectors exist on no other page, so the
 * probe would throw "ölçüm alınamadı" instead of failing an assertion. */
const LIVE = "http://127.0.0.1:8099/";
const ARCH = "http://127.0.0.1:8100/";
const WEEK = "http://127.0.0.1:8103/";

try {
  execFileSync("curl", ["-s", "-o", "/dev/null", "--max-time", "3", LIVE], { timeout: 5000 });
} catch (e) {
  console.log("\nservis ayakta değil, görsel testler atlandı (" + LIVE + ")");
  process.exit(0);
}

console.log("\n1. kapalı panel (canlı)");
{
  const r = probe(LIVE, `(() => {
    const c = document.querySelector("#chat"), g = document.querySelector("#grip");
    const R = e => +e.getBoundingClientRect().height.toFixed(1);
    return { chatH: R(c), gripH: R(g), top: +c.getBoundingClientRect().top.toFixed(0),
             vh: innerHeight, cls: c.className };
  })()`);
  t("panel 46px'e kilitli", () => {
    if (Math.abs(r.chatH - 46) > 1) throw new Error("chatH=" + r.chatH);
  });
  t("tutamaç 46px (flex ile ezilmemiş)", () => {
    if (Math.abs(r.gripH - 46) > 1) throw new Error("gripH=" + r.gripH + " — flex-shrink yiyor");
  });
  t("panel ekranın en altına yapışık", () => {
    if (Math.abs(r.top + 46 - r.vh) > 2) throw new Error("top=" + r.top + " vh=" + r.vh);
  });
  shot(LIVE, "live-closed.png");
}

console.log("\n2. açık panel (canlı)");
{
  const r = probe(LIVE, `(() => {
    const c = document.querySelector("#chat"), m = document.querySelector("#msgs");
    const hd = document.querySelector("#chathead"), tabs = document.querySelector(".subtabs");
    const g = document.querySelector("#grip");
    const R = e => e ? +e.getBoundingClientRect().height.toFixed(1) : -1;
    return { chatH: R(c), msgsH: R(m), headH: R(hd), gripH: R(g),
             tabsHidden: tabs ? tabs.hidden : null,
             tabsDisplay: tabs ? getComputedStyle(tabs).display : null,
             vh: innerHeight };
  })()`, { click: "#grip" });
  t("panel %62 (tabloyu tamamen örtmüyor)", () => {
    const want = Math.round(r.vh * 0.62);
    if (Math.abs(r.chatH - want) > 8) throw new Error("chatH=" + r.chatH + " beklenen~" + want);
  });
  t("mesaj alanı en az 250px (tutamaç açıkken incelir)", () => {
    if (r.msgsH < 250) throw new Error("msgsH=" + r.msgsH);
  });
  t("açıkken tutamaç 30px, kapalıyken 46px", () => {
    if (Math.abs(r.gripH - 30) > 1) throw new Error("açık gripH=" + r.gripH);
  });
  t("chathead tek satır (<=46px)", () => {
    if (r.headH > 46) throw new Error("headH=" + r.headH + " — flex-wrap sarıyor");
  });
  t("telefonda sekme çubuğu gizli", () => {
    if (r.tabsDisplay !== "none") throw new Error("display=" + r.tabsDisplay + " ([hidden] eziliyor)");
  });
  shot(LIVE, "live-open.png", { click: "#grip" });
}

console.log("\n3. klavye açık (canlı)");
{
  const r = probe(LIVE, `(async () => {
    const vv = window.visualViewport, c = document.querySelector("#chat");
    const R = e => +e.getBoundingClientRect().height.toFixed(1);
    const before = R(c);
    Object.defineProperty(vv, "height", { value: 550, configurable: true });
    Object.defineProperty(vv, "offsetTop", { value: 0, configurable: true });
    vv.dispatchEvent(new Event("resize"));
    await new Promise(r => setTimeout(r, 450));
    return { before, after: R(c), bottom: c.style.bottom, vh: innerHeight };
  })()`, { click: "#grip" });
  t("klavye açılınca panel yukarı itilir", () => {
    const b = parseInt(r.bottom, 10);
    if (!(b >= 300 && b <= 400)) throw new Error("bottom=" + r.bottom);
  });
  t("klavye açıkken panel görünür alanı kaplar", () => {
    if (Math.abs(r.after - 550) > 8) throw new Error("after=" + r.after);
  });
}

console.log("\n4. geçici puan durumu (canlı, mobil)");
{
  const r = probe(LIVE, `(() => {
    const pane = document.querySelector("#pane-points"), slot = document.querySelector("#provslot");
    return { inSlot: !!(pane && slot && pane.parentElement === slot),
             paneHidden: pane ? pane.hidden : null,
             slotHidden: slot ? slot.hidden : null };
  })()`);
  t("pane panelden çıkıp Race Control altına taşınmış", () => {
    if (!r.inSlot) throw new Error("hâlâ panelin içinde");
  });
  t("taşınan pane görünür", () => {
    if (r.paneHidden || r.slotHidden) throw new Error("gizli kalmış");
  });
}

console.log("\n5. arşiv paneli");
{
  const closed = probe(ARCH, `(() => {
    const c = document.querySelector("#chatpanel"), g = document.querySelector("#grip");
    const R = e => +e.getBoundingClientRect().height.toFixed(1);
    return { chatH: R(c), gripH: R(g) };
  })()`);
  t("arşiv kapalı panel 46px", () => {
    if (Math.abs(closed.chatH - 46) > 1) throw new Error("chatH=" + closed.chatH);
  });
  t("arşiv tutamaç 46px", () => {
    if (Math.abs(closed.gripH - 46) > 1) throw new Error("gripH=" + closed.gripH);
  });

  const open = probe(ARCH, `(() => {
    const c = document.querySelector("#chatpanel"), m = document.querySelector("#msgs");
    const R = e => +e.getBoundingClientRect().height.toFixed(1);
    return { chatH: R(c), msgsH: R(m), vh: innerHeight };
  })()`, { click: "#grip" });
  t("arşiv açık panel %62", () => {
    const want = Math.round(open.vh * 0.62);
    if (Math.abs(open.chatH - want) > 8) throw new Error("chatH=" + open.chatH + " beklenen~" + want);
  });
  t("arşiv mesaj alanı en az 150px", () => {
    if (open.msgsH < 150) throw new Error("msgsH=" + open.msgsH);
  });
  shot(ARCH, "archive-open.png", { click: "#grip" });
}

console.log("\n6. masaüstü — mobil kurallar sızmıyor");
{
  const r = probe(LIVE, `(() => {
    const c = document.querySelector("#chat"), p = document.querySelector("#pane-points");
    const tabs = document.querySelector(".subtabs"), t = document.querySelector("#chathead .t");
    return {
      chatPos: getComputedStyle(c).position,
      paneInChat: p.parentElement === c,
      tabsHidden: tabs.hidden,
      titleClipped: t.scrollWidth > t.clientWidth + 1,
      headH: +document.querySelector("#chathead").getBoundingClientRect().height.toFixed(0),
    };
  })()`, { width: 1280, height: 900 });
  t("panel masaüstünde akışta (position:static)", () => {
    if (r.chatPos !== "static") throw new Error("pos=" + r.chatPos);
  });
  t("geçici tablo panelin İÇİNDE (taşınmamış)", () => {
    if (!r.paneInChat) throw new Error("mobil taşıma masaüstüne sızmış");
  });
  t("sekmeler masaüstünde görünür", () => {
    if (r.tabsHidden) throw new Error("sekmeler gizli");
  });
  t("başlık satırı tek satır ve kırpılmamış", () => {
    if (r.titleClipped) throw new Error("başlık kırpılıyor (div çok dar)");
    if (r.headH > 62) throw new Error("chathead " + r.headH + "px — sarıyor");
  });
  shot(LIVE, "desktop.png", { width: 1280, height: 900 });
}

console.log("\n7. başlık (header) — sarma ve taşma");
{
  const hdr = `(() => {
    const sh = document.querySelector("header.shell");
    if (!sh) return { yok: true };
    const r = sh.getBoundingClientRect();
    return {
      h: +r.height.toFixed(0),
      childOverflow: [...sh.children].some(k => k.getBoundingClientRect().bottom > r.bottom + 1),
      kids: [...sh.children].filter(k => k.getBoundingClientRect().height > 0).length,
    };
  })()`;

  for (const [name, url] of [["canlı", LIVE], ["arşiv", ARCH],
                             ["puan", "http://127.0.0.1:8101/"]]) {
    const r = probe(url, hdr);
    t(name + ": header çocukları taşmıyor", () => {
      // `min-height:46px` flex sarmayı engelliyordu: shell-end başlığın
      // dışına çıkıyor, alt şerit kırpılıyordu.
      if (r.childOverflow) throw new Error("çocuk header'ın dışına çıkıyor");
    });
    t(name + ": header telefonda <=90px", () => {
      if (r.h > 90) throw new Error("h=" + r.h + "px — ekranın çok yerini yiyor");
    });
  }
}

console.log("\n8. iki sohbet paneli piksel hizası (mobil)");
{
  const cmp = `(() => {
    const q = s => document.querySelector(s);
    const R = e => { if (!e) return null; const r = e.getBoundingClientRect(); return {h:+r.height.toFixed(0), top:+r.top.toFixed(0)}; };
    return { msgs: R(q("#msgs")), chips: R(q("#chips")), usage: R(q("#usagebar")),
             form: R(q("#ask") || q("#f")), srcline: R(q("#srcline")),
             panelW: +q("#chat, #chatpanel").getBoundingClientRect().width.toFixed(0) };
  })()`;

  const a = probe(ARCH, cmp, { click: "#grip" });
  const l = probe(LIVE, cmp, { click: "#grip" });

  t("sohbet kolonu iki sayfada aynı genişlikte", () => {
    // Genişlik farkı doğrudan hizayı bozuyordu: 379px'lik kolonda usage
    // şeridi 2 satıra düşüp 45px oluyor, altındaki her şey 25px kayıyordu.
    if (Math.abs(a.panelW - l.panelW) > 2) {
      throw new Error("arşiv " + a.panelW + "px vs canlı " + l.panelW + "px");
    }
  });

  // Form ve altındaki bilgi satırları SABİT konumda olmalı: ikisi de panelin
  // dibine yaslı, aradaki fark içerikten gelmemeli.
  for (const k of ["usage", "form", "srcline"]) {
    t(k + ": iki sayfada aynı yükseklik", () => {
      if (!a[k] || !l[k]) throw new Error("ölçüm yok");
      if (Math.abs(a[k].h - l[k].h) > 1) {
        throw new Error("arşiv " + a[k].h + "px vs canlı " + l[k].h + "px");
      }
    });
    t(k + ": iki sayfada aynı konum", () => {
      if (Math.abs(a[k].top - l[k].top) > 3) {
        throw new Error("arşiv @" + a[k].top + " vs canlı @" + l[k].top);
      }
    });
  }

  // Çiplerin YÜKSEKLİĞİ metne göre değişir (farklı öneriler), ama ALT
  // kenarları aynı olmalı: ikisi de usage şeridinin hemen üstünde bitiyor.
  t("chips: iki sayfada aynı ALT kenar", () => {
    const ab = a.chips.top + a.chips.h, lb = l.chips.top + l.chips.h;
    if (Math.abs(ab - lb) > 3) throw new Error("arşiv @" + ab + " vs canlı @" + lb);
  });

  t("MASAÜSTÜ: alt bilgi satırları iki sayfada hizalı", () => {
    const cmpD = `(() => {
      const q = s => document.querySelector(s);
      const R = e => { if (!e) return null; const r = e.getBoundingClientRect(); return {h:+r.height.toFixed(0), top:+r.top.toFixed(0), w:+r.width.toFixed(0)}; };
      return { usage: R(q("#usagebar")), form: R(q("#ask") || q("#f")),
               srcline: R(q("#srcline")),
               panelW: +q("#chat, #chatpanel").getBoundingClientRect().width.toFixed(0) };
    })()`;
    const ad = probe(ARCH, cmpD, { width: 1280, height: 900 });
    const ld = probe(LIVE, cmpD, { width: 1280, height: 900 });
    if (Math.abs(ad.panelW - ld.panelW) > 2) {
      throw new Error("kolon genişliği: arşiv " + ad.panelW + " vs canlı " + ld.panelW);
    }
    for (const k of ["usage", "form", "srcline"]) {
      if (Math.abs(ad[k].top - ld[k].top) > 3) {
        throw new Error(k + " konumu: arşiv @" + ad[k].top + " vs canlı @" + ld[k].top);
      }
      if (Math.abs(ad[k].h - ld[k].h) > 1) {
        throw new Error(k + " yüksekliği: arşiv " + ad[k].h + " vs canlı " + ld[k].h);
      }
    }
  });

  t("arşiv kapalı panelde form görünmüyor", () => {
    // sticky:bottom:0 hatası formu 46px'lik şeride çekiyordu.
    const r = probe(ARCH, `(() => {
      const p = document.querySelector("#chatpanel"), a = document.querySelector("#ask");
      const pr = p.getBoundingClientRect(), ar = a.getBoundingClientRect();
      return { panelTop: +pr.top.toFixed(0), panelH: +pr.height.toFixed(0),
               formTop: +ar.top.toFixed(0), inside: ar.top >= pr.top && ar.top < pr.bottom };
    })()`);
    if (r.panelH > 50) throw new Error("panel kapalı değil: " + r.panelH);
    if (r.inside) throw new Error("form kapalı panelin içinde görünüyor (sticky hatası)");
  });
}

console.log("\n6. hafta sonu ızgarası (telefon 390px)");
{
  let r = null;
  try {
    r = probe(WEEK, `(() => {
      const cards = [...document.querySelectorAll("#cards .scard")];
      const R = e => e.getBoundingClientRect();
      const boxes = cards.map(c => R(c));
      const rows = new Set(boxes.map(b => Math.round(b.top)));
      const badge = document.querySelector(".scard .badge-state");
      const label = document.querySelector(".scard .k");
      const hl = document.querySelector(".scard .hl");
      const br = badge ? R(badge) : null;
      const lr = label ? R(label) : null;
      const hr = hl ? R(hl) : null;
      return {
        n: cards.length,
        cols: cards.length ? Math.round(cards[0].closest("#cards").getBoundingClientRect().width
              / (boxes[0].width + 8)) : 0,
        minH: boxes.length ? Math.min(...boxes.map(b => +b.height.toFixed(1))) : 0,
        overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        badgeTop: br ? +br.top.toFixed(0) : null,
        labelTop: lr ? +lr.top.toFixed(0) : null,
        headTop: hr ? +hr.top.toFixed(0) : null,
        headClipped: hl ? (hl.scrollWidth > hl.clientWidth + 1) : false,
        rows: rows.size,
      };
    })()`, { width: 390, height: 844 });
  } catch (e) {
    console.log("  atlandı (hafta sonu yüzeyi yok): " + e.message);
  }
  if (r) {
    t("kart ızgarası dolu", () => {
      if (!r.n) throw new Error("hiç kart yok");
    });
    t("telefonda iki sütun", () => {
      if (r.cols !== 2) throw new Error("sütun: " + r.cols);
    });
    t("dokunma hedefi >= 44px", () => {
      if (r.minH < 44) throw new Error("kart yüksekliği: " + r.minH);
    });
    t("yatay taşma yok", () => {
      if (r.overflowX > 1) throw new Error("scrollWidth fazlası: " + r.overflowX);
    });
    t("rozet seans adıyla aynı satırda, sayının üstünde", () => {
      if (r.badgeTop === null || r.labelTop === null || r.headTop === null) {
        throw new Error("ölçüm eksik");
      }
      if (Math.abs(r.badgeTop - r.labelTop) > 4) {
        throw new Error("rozet/etiket satırı: " + r.badgeTop + " vs " + r.labelTop);
      }
      if (r.headTop <= r.labelTop) throw new Error("başlık rozetin üstünde");
    });
    t("kart başlığı kırpılmıyor", () => {
      if (r.headClipped) throw new Error("headline ellipsis'e düşmüş");
    });
  }
}

console.log(bad === 0 ? "\n" + n + " DÜZEN TESTİ GEÇTİ" : "\n" + bad + "/" + n + " HATA");
process.exit(bad ? 1 : 0);
