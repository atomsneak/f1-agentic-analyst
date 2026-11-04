#!/usr/bin/env node
/* Gerçek tarayıcıdan ekran görüntüsü — CDP üzerinden.
 *
 * Neden bu dosya var: bu makinede `/snap/bin/chromium` ve `firefox` snap-confine
 * yüzünden çalışmıyor ("has elevated permissions and is not confined"). Yani
 * sayfaların telefonda nasıl göründüğünü görmenin bir yolu yoktu ve yerleşim
 * hataları ekran görüntüsüyle değil tahminle düzeltiliyordu.
 *
 * Çözüm: Chrome for Testing'in `chrome-headless-shell` ikilisi snap'e
 * bağlı değil, doğrudan indirilip çalıştırılabiliyor:
 *   ~/.local/share/f1-agentic-analyst/tools/chrome-headless-shell-linux64/
 *
 * `--screenshot` bayrağı NEDEN kullanılmıyor: canlı sayfa bir EventSource
 * açıyor, ağ hiç "boşta" olmuyor, headless asılı kalıyor. CDP ile kareyi biz
 * istiyoruz, tarayıcının kararına bırakmıyoruz.
 *
 * Kullanım:
 *   node tools/shot.js URL ÇIKTI.png [genişlik] [yükseklik] [bekle_ms]
 *   node tools/shot.js ... --eval "js ifadesi"     (sayfada JS çalıştır)
 *   node tools/shot.js ... --click "#selector"     (önce tıkla)
 *   node tools/shot.js ... --full                  (tam sayfa)
 */
"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const HOME = os.homedir();
const SHELL_BIN = path.join(HOME,
  ".local/share/f1-agentic-analyst/tools/chrome-headless-shell-linux64/chrome-headless-shell");

function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i > 0 ? process.argv[i + 1] : def;
}
const flag = (name) => process.argv.includes("--" + name);

const url = process.argv[2];
const out = process.argv[3] || "/tmp/shot.png";
const width = Number(process.argv[4] || 412);
const height = Number(process.argv[5] || 915);
const settle = Number(process.argv[6] || 2500);
const evalJs = arg("eval", null);
const clickSel = arg("click", null);
const fullPage = flag("full");

if (!url) {
  console.error("kullanım: node tools/shot.js URL CIKTI.png [g] [y] [bekle_ms]");
  process.exit(2);
}

const PORT = 9333 + (process.pid % 500);
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "f1shot-"));

const child = spawn(SHELL_BIN, [
  "--headless",
  "--no-sandbox",
  "--disable-gpu",
  "--disable-dev-shm-usage",
  "--hide-scrollbars",
  "--remote-debugging-port=" + PORT,
  "--user-data-dir=" + PROFILE,
  "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });

let childErr = "";
child.stderr.on("data", (d) => { childErr += d.toString(); });

function get(p) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: "127.0.0.1", port: PORT, path: p }, (res) => {
      let b = "";
      res.on("data", (c) => { b += c; });
      res.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    });
    req.on("error", reject);
    req.setTimeout(1500, () => req.destroy(new Error("timeout")));
  });
}

async function waitForDevtools() {
  for (let i = 0; i < 80; i++) {
    try { return await get("/json/list"); } catch (e) { /* henüz hazır değil */ }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error("devtools açılmadı:\n" + childErr);
}

class Cdp {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
    });
  }
  open() {
    return new Promise((res, rej) => {
      this.ws.addEventListener("open", res, { once: true });
      this.ws.addEventListener("error", rej, { once: true });
    });
  }
  send(method, params) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("CDP zaman aşımı: " + method));
        }
      }, 20000);
    });
  }
}

(async () => {
  let cdp;
  try {
    const targets = await waitForDevtools();
    const page = targets.find((t) => t.type === "page");
    if (!page) throw new Error("sayfa hedefi yok");
    cdp = new Cdp(page.webSocketDebuggerUrl);
    await cdp.open();

    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    // Telefon görünür alanı; DPR 2 yaygın bir gerçek cihaz yoğunluğu (amiral gemisi ~3,
    // ama 2 dosya boyutunu makul tutuyor ve yerleşimi aynı gösteriyor).
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 2, mobile: true,
    });
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });

    await cdp.send("Page.navigate", { url });
    // Ağ boşta olmayabilir (SSE); sabit bekleme yeterli ve öngörülebilir.
    await new Promise((r) => setTimeout(r, settle));

    if (clickSel) {
      const r = await cdp.send("Runtime.evaluate", {
        expression: `(()=>{const e=document.querySelector(${JSON.stringify(clickSel)});
          if(!e) return 'yok'; e.click(); return 'ok';})()`,
        returnByValue: true,
      });
      if (r.result.value !== "ok") throw new Error("tıklanacak öğe yok: " + clickSel);
      await new Promise((r2) => setTimeout(r2, 700)); // geçiş animasyonu
    }

    if (evalJs) {
      const r = await cdp.send("Runtime.evaluate", {
        expression: evalJs, returnByValue: true, awaitPromise: true,
      });
      if (r.exceptionDetails) {
        throw new Error("JS hatası: " + JSON.stringify(r.exceptionDetails.text || r.exceptionDetails));
      }
      if (r.result && r.result.value !== undefined) {
        console.log("eval ->", JSON.stringify(r.result.value));
      }
      await new Promise((r2) => setTimeout(r2, 400));
    }

    let clip;
    if (fullPage) {
      const m = await cdp.send("Page.getLayoutMetrics");
      const cs = m.cssContentSize || m.contentSize;
      clip = { x: 0, y: 0, width: cs.width, height: cs.height, scale: 1 };
    }

    const shot = await cdp.send("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: !!fullPage, ...(clip ? { clip } : {}),
    });
    fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
    fs.writeFileSync(out, Buffer.from(shot.data, "base64"));
    console.log("yazıldı:", out);
  } catch (e) {
    console.error("HATA:", e.message);
    process.exitCode = 1;
  } finally {
    try { if (cdp) cdp.ws.close(); } catch (e) { /* önemsiz */ }
    child.kill("SIGKILL");
    setTimeout(() => { try { fs.rmSync(PROFILE, { recursive: true, force: true }); } catch (e) {} }, 200);
  }
})();
