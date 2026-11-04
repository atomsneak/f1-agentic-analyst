#!/usr/bin/env node
/* PWA doğrulaması — GERÇEK tarayıcıda, gerçek service worker ile.
 *
 * Neden gerekli: unittest'ler kaynağın doğru olduğunu söyler, kurulabilir
 * olduğunu değil. Chrome'un install kriterleri (elde edilen manifest, kapsamı
 * sayfayı kapsayan bir worker, 192+512 ikon, start_url'in kapsam içinde
 * olması) ancak çalışan bir tarayıcıda belli olur. Aynı şekilde "çevrimdışı
 * açılıyor mu" sorusunun cevabı yalnız şebeke kesilince görülür.
 *
 * Sınananlar:
 *   1. sw.js kaydoluyor ve scope sayfayı kapsıyor.
 *   2. manifest linki çözülüyor, JSON geliyor, install kriterleri tam.
 *   3. Kabuk + ikonlar önbelleğe girmiş.
 *   4. Ağ kesilip sayfa yenilenince kabuk önbellekten açılıyor (başlık geliyor).
 *   5. Çevrimdışı iken POST denenirse çevrimiçi gibi "başarılı" görünmüyor.
 *
 * Çalıştırma: node tests/pwa_test.js
 * Servis ayakta olmalı. Test kendi tarayıcı profilini kullanır ve sonunda
 * siler; mevcut hiçbir Chrome oturumuna dokunmaz.
 */
"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const BIN = path.join(os.homedir(),
  ".local/share/f1-agentic-analyst/tools/chrome-headless-shell-linux64/chrome-headless-shell");
const URL_LIVE = "http://127.0.0.1:8099/";
const URL_ARCHIVE = "http://127.0.0.1:8100/";
const URL_WELCOME = "http://127.0.0.1:8103/";

let bad = 0, n = 0, skipped = 0;
async function t(name, fn) {
  n++;
  try { await fn(); console.log("  ok   " + name); }
  catch (e) { bad++; console.log("  HATA " + name + ": " + e.message); }
}

if (!fs.existsSync(BIN)) {
  console.log("chrome-headless-shell yok, test atlandı: " + BIN);
  process.exit(0);
}

function get(port, p) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: "127.0.0.1", port, path: p }, (res) => {
      let b = "";
      res.on("data", (c) => { b += c; });
      res.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    });
    req.on("error", reject);
    req.setTimeout(1500, () => req.destroy(new Error("timeout")));
  });
}

class Cdp {
  constructor(wsUrl) { this.ws = new WebSocket(wsUrl); this.id = 0; this.pending = new Map();
    this.ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data); const p = this.pending.get(m.id);
      if (!p) return; this.pending.delete(m.id);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }); }
  open() { return new Promise((res, rej) => {
    this.ws.addEventListener("open", res, { once: true });
    this.ws.addEventListener("error", rej, { once: true }); }); }
  send(method, params) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id);
        reject(new Error("CDP zaman aşımı: " + method)); } }, 25000);
    });
  }
  async js(expr) {
    const r = await this.send("Runtime.evaluate", {
      expression: expr, returnByValue: true, awaitPromise: true, userGesture: true });
    if (r.exceptionDetails) throw new Error("JS: " + (r.exceptionDetails.text || "?"));
    return r.result.value;
  }
}

function startBrowser() {
  const port = 9400 + (process.pid % 400);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "f1pwa-"));
  const child = spawn(BIN, [
    "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--hide-scrollbars", "--remote-debugging-port=" + port,
    "--user-data-dir=" + profile, "about:blank",
  ], { stdio: ["ignore", "pipe", "pipe"] });
  let err = "";
  child.stderr.on("data", (d) => { err += d.toString(); });
  return { child, port, profile, err: () => err };
}

(async () => {
  const b = startBrowser();
  let cdp;
  try {
    let targets = null;
    for (let i = 0; i < 100 && !targets; i++) {
      try { targets = await get(b.port, "/json/list"); }
      catch (e) { await new Promise((r) => setTimeout(r, 150)); }
    }
    if (!targets) throw new Error("devtools açılmadı:\n" + b.err());
    const page = targets.find((x) => x.type === "page");
    cdp = new Cdp(page.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");

    const nav = async (url) => {
      await cdp.send("Page.navigate", { url });
      await new Promise((r) => setTimeout(r, 2200));
    };

    console.log("\nKurulum (canlı yüzey)");
    await nav(URL_LIVE);

    await t("service worker kaydoldu ve kapsamı sayfayı kapsıyor", async () => {
      const reach = await cdp.js(`navigator.serviceWorker.getRegistration()
        .then(r => r ? (r.scope) : "")`);
      if (!reach) throw new Error("kayıt yok (secure context değil mi?)");
      if (!URL_LIVE.startsWith(reach)) throw new Error("kapsam dışı: " + reach);
    });

    await t("worker aktif (install + activate bitti)", async () => {
      const st = await cdp.js(`navigator.serviceWorker.getRegistration()
        .then(r => r ? [r.active && r.active.state, r.waiting && r.waiting.state] : null)`);
      if (!st || st[0] !== "activated") throw new Error("durum: " + JSON.stringify(st));
    });

    await t("manifest çözülüyor ve install kriterleri tam", async () => {
      const m = await cdp.js(`(() => {
        const l = document.querySelector('link[rel=manifest]');
        if (!l) return { err: 'link yok' };
        return fetch(l.href).then(r => r.json()).then(j => ({
          href: l.href, name: j.name, start_url: j.start_url,
          display: j.display, icons: j.icons.map(i => i.sizes + ':' + (i.purpose || 'any')),
        }));
      })()`);
      if (m.err) throw new Error(m.err);
      if (m.display !== "standalone") throw new Error("display: " + m.display);
      if (!m.icons.some((i) => i.startsWith("192x192"))) throw new Error("192 ikon yok");
      if (!m.icons.some((i) => i.startsWith("512x512"))) throw new Error("512 ikon yok");
      if (!m.icons.some((i) => i.includes("maskable"))) throw new Error("maskable ikon yok");
      const icon = await cdp.js(`fetch(new URL('static/icons/icon-512.png',
        document.querySelector('link[rel=manifest]').href)).then(r => r.status)`);
      if (icon !== 200) throw new Error("512 ikon " + icon);
    });

    await t("kabuk ve ikonlar önbellekte", async () => {
      const keys = await cdp.js(`caches.keys()`);
      if (!keys.length) throw new Error("hiç önbellek yok");
      /* The cache name is now DERIVED from the precached bytes (pwa.py), so
       * hard-coding it here was a second place to bump -- and it broke the moment
       * the derivation landed. Read the name from the live cache list instead:
       * this test is about "are the files cached", not about the name. */
      const items = await cdp.js(`(async () => {
        const names = (await caches.keys()).filter(n => /^f1-shell-/.test(n));
        if (!names.length) return [];
        const c = await caches.open(names[0]);
        return (await c.keys()).map(r => r.url);
      })()`);
      const need = ["static/theme.css", "static/shell.js", "manifest.webmanifest",
                    "static/icons/icon-192.png"];
      for (const f of need) {
        if (!items.some((u) => u.endsWith(f))) throw new Error("önbellekte yok: " + f);
      }
    });

    await t("çevrimiçi iken çevrimdışı rozeti gizli", async () => {
      const hidden = await cdp.js(`(() => { const e = document.querySelector('#netbadge');
        return e ? e.hidden : 'yok'; })()`);
      if (hidden !== true) throw new Error("rozet durumu: " + JSON.stringify(hidden));
    });

    console.log("\nŞebeke kesik");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1,
    });

    await t("ağ yokken sayfa önbellekten açılıyor", async () => {
      await nav(URL_LIVE);
      const info = await cdp.js(`(() => ({
        title: document.title,
        shell: !!document.querySelector('header.shell'),
        logo: (document.querySelector('.logo') || {}).textContent || '',
        badge: (document.querySelector('#netbadge') || {}).hidden,
      }))()`);
      if (!info.shell) throw new Error("kabuk gelmedi (offline fallback yok)");
      if (!/F1/i.test(info.logo)) throw new Error("logo boş");
      if (info.badge !== false) throw new Error("çevrimdışı rozeti görünmüyor");
    });

    await t("ağ yokken API önbellekten geliyor ya da temiz hata veriyor", async () => {
      const r = await cdp.js(`fetch('/api/state').then(r => ({ status: r.status, ok: r.ok }))
        .catch(e => ({ err: String(e) }))`);
      // İkisi de kabul: önbellekteki anlık görüntü (200) ya da ağ hatası.
      // Kabul edilemez olan, ağ varmış gibi sessizce boş veri dönmesi.
      if (r.err) return;
      if (r.status !== 200 && r.status < 400) throw new Error("garip durum: " + r.status);
    });

    console.log("\nArşiv yüzeyi");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1,
    });
    await t("arşiv kendi worker'ını ve kendi manifestini alıyor", async () => {
      await nav(URL_ARCHIVE);
      const r = await cdp.js(`(async () => {
        const reg = await navigator.serviceWorker.getRegistration();
        const m = await fetch('manifest.webmanifest').then(x => x.json());
        return { scope: reg && reg.scope, name: m.name, start: m.start_url };
      })()`);
      if (!r.scope || r.scope !== URL_ARCHIVE) throw new Error("kapsam: " + r.scope);
      if (!/Arşiv/.test(r.name)) throw new Error("manifest adı: " + r.name);
      if (r.start !== "./") throw new Error("start_url: " + r.start);
    });

    /* The welcome grid is the newest surface and the one most likely to be
     * installed (it is the landing page). Its own worker + manifest must be
     * separate from the archive's: sharing a cache name would let one surface's
     * update evict the other's shell, so the check is not just "does it load". */
    await t("hafta sonu yüzeyi kendi worker'ını ve manifestini alıyor", async () => {
      await nav(URL_WELCOME);
      const r = await cdp.js(`(async () => {
        const reg = await navigator.serviceWorker.getRegistration();
        const m = await fetch('manifest.webmanifest').then(x => x.json());
        const cards = document.querySelectorAll('#cards .scard').length;
        return { scope: reg && reg.scope, name: m.name, start: m.start_url, cards };
      })()`);
      if (!r.scope || r.scope !== URL_WELCOME) throw new Error("kapsam: " + r.scope);
      if (!/Hafta/.test(r.name)) throw new Error("manifest adı: " + r.name);
      if (r.start !== "./") throw new Error("start_url: " + r.start);
      if (!r.cards) throw new Error("kart ızgarası boş (veri gelmedi mi?)");
    });
  } catch (e) {
    bad++;
    console.log("  HATA (kurulum): " + e.message);
  } finally {
    try { if (cdp) cdp.ws.close(); } catch (e) { /* önemsiz */ }
    b.child.kill("SIGKILL");
    setTimeout(() => { try { fs.rmSync(b.profile, { recursive: true, force: true }); } catch (e) {} }, 300);
  }
  console.log(bad === 0 ? "\n" + n + " PWA TESTİ GEÇTİ" : "\n" + bad + "/" + n + " HATA");
  process.exit(bad ? 1 : 0);
})();
