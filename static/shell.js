/* F1 Agentic Analyst — shared shell behaviour.
 *
 * Two jobs, both small:
 *
 * 1. Cross-surface links. The surfaces run on three ports of the same host, and
 *    that host is not always 127.0.0.1 -- a page opened at 192.0.2.10:8099
 *    would get a hard-coded localhost link pointing at its own loopback. So each
 *    nav link declares only its port (data-port) and this file swaps in the host
 *    the page was actually served from. A link keeps a working href by default
 *    (127.0.0.1), so the nav is usable even with JavaScript off.
 *
 * 2. The two formatting helpers every surface needs: an age in words, and an
 *    explicit "unknown" marker. Deliberately tiny and dependency-free.
 */
(function () {
  "use strict";

  // ---- 1. cross-surface nav ----

  /* Each surface listens on its own port and is reached at its own origin, so
     a link to another surface is just that port on the same host. No path
     prefix: this build is meant to run on one machine, on loopback. */
  function surfaceUrl(port) {
    var host = location.hostname || "127.0.0.1";
    var proto = location.protocol === "https:" ? "https:" : "http:";
    return proto + "//" + host + ":" + port + "/";
  }

  /* Kept as a separate name so callers that ask for "where does this surface
     live" keep working; with no path prefix it is the same answer. */
  var surfaceHref = surfaceUrl;

  /* Applies data-port links present in the DOM right now. Called once at load
     and again by any surface that injects a link after load. */
  function rewriteNav() {
    var links = document.querySelectorAll("a[data-port]");
    for (var i = 0; i < links.length; i++) {
      var port = links[i].getAttribute("data-port");
      if (!port) continue;
      links[i].href = surfaceHref(port);
    }
  }

  // ---- 2. PWA: install + an honest offline badge ----

  /* Registers pwa.py's worker. The path is deliberately relative: the worker
     is served at the surface root, and the browser derives its scope from the
     page's own URL, so the same line is correct on any host and port -- and
     would stay correct under a path prefix. */
  function registerSW() {
    if (!("serviceWorker" in navigator)) return;
    /* http:// over a plain LAN address is NOT a secure context, so
       register() would reject there. That is expected, not a page bug -- skip
       it instead of logging a failure on every single load. */
    if (!window.isSecureContext) return;
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("sw.js").then(null, function (e) {
        console.warn("service worker kaydı başarısız:", (e && e.message) || e);
      });
    });
  }

  /* One badge in the header while the browser reports no connection. It has to
     exist because the worker serves both the shell and the last API snapshot
     from cache: without it an offline page would look like a live one showing
     yesterday's numbers. Reuses the shared `.badge.warn` style -- no new CSS,
     and the header already wraps. */
  function netBadge() {
    var shell = document.querySelector("header.shell");
    if (!shell) return;
    var el = document.createElement("span");
    el.id = "netbadge";
    el.className = "badge warn";
    el.title = "Tarayıcı çevrimdışı: sayfa önbellekten açıldı, canlı veri gelmiyor.";
    el.textContent = "çevrimdışı";
    el.hidden = navigator.onLine !== false;
    var end = shell.querySelector(".shell-end");
    shell.insertBefore(el, end || null);
    function sync() { el.hidden = navigator.onLine !== false; }
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
  }


  // ---- 4. AI sağlayıcı/model seçimi ----
  /*
   * Why it lives in shell.js: the live page and the archive page each have a chat
   * panel and each is a separate process. Written twice, the two dialogs drift --
   * this project has already paid for that once (the markdown formatter existing
   * only in index.html, so the archive printed literal asterisks). One copy, two
   * surfaces, one endpoint (/api/ai) served by both.
   *
   * The API key never comes back from the server, so this code can only ever
   * *send* one. The list shows a 4-character hint, which is enough to tell two
   * keys apart and useless to anyone who reads it.
   */
  function aiPanel() {
    var dlg = document.getElementById("aidlg");
    var btn = document.getElementById("aibtn");
    if (!dlg || !btn) return;

    var el = function (id) { return document.getElementById(id); };
    var state = { profiles: [], current: null };

    function show(msg, kind) {
      var st = el("aistatus");
      if (!st) return;
      st.textContent = msg || "";
      st.className = "aistatus" + (kind ? " " + kind : "");
    }

    function fill(p) {
      state.current = p || null;
      el("ailabel").value = (p && p.label) || "";
      el("aibase").value = (p && p.base_url) || "";
      el("aimodel").value = (p && p.model) || "";
      el("aikey").value = "";                       // never prefilled, never echoed
      el("aidel").disabled = !(p && p.id);
    }

    function render(st) {
      state.profiles = (st && st.profiles) || [];
      var a = (st && st.active) || {};
      var hint = el("aihint");
      if (hint) {
        hint.textContent = a.source === "profile"
          ? "Şu an: " + (a.label || a.model) + " · " + a.model
            + (a.key_hint ? " · anahtar " + a.key_hint : "")
          : a.source === "env"
            ? "Şu an: " + a.model + " (anahtar F1_AI_KEY ortam değişkeninde)"
            : "Henüz sağlayıcı yok — aşağıdan ekle, yoksa asistan cevap veremez.";
      }
      var list = el("ailist");
      if (list) {
        list.innerHTML = state.profiles.map(function (p) {
          var cur = p.is_default ? ' aria-current="true"' : "";
          var tag = p.has_key ? p.key_hint : "anahtarsız";
          return '<li data-id="' + p.id + '"' + cur + ' title="Bu profili düzenle">'
            + '<span class="nm">' + F1.esc(p.label || p.model) + "</span>"
            + '<span class="tag">' + F1.esc(p.model) + " · " + F1.esc(tag) + "</span></li>";
        }).join("");
        Array.prototype.forEach.call(list.querySelectorAll("li"), function (li) {
          li.onclick = function () {
            var id = Number(li.getAttribute("data-id"));
            var p = state.profiles.filter(function (x) { return x.id === id; })[0];
            if (p) { fill(p); show("Düzenleniyor: " + (p.label || p.model), ""); }
          };
        });
      }
    }

    function load() {
      return F1.fetchJSON("api/ai").then(function (st) {
        render(st);
        var d = (state.profiles.filter(function (p) { return p.is_default; })[0]
                 || state.profiles[0] || null);
        fill(d);
      }).catch(function (e) {
        show("Ayarlar okunamadı: " + (e.message || e), "bad");
      });
    }

    btn.onclick = function () {
      show("", "");
      load();
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
    };

    el("aitest").onclick = function () {
      show("Test ediliyor…", "");
      F1.fetchJSON("api/ai/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: el("aibase").value.trim(),
          model: el("aimodel").value.trim(),
          api_key: el("aikey").value.trim() || null,
        }),
      }).then(function (r) {
        if (r && r.ok) show("Bağlantı çalışıyor." + (r.model_echo ? " (" + r.model_echo + ")" : ""), "ok");
        else show("Bağlantı başarısız: " + ((r && r.error) || "bilinmeyen hata"), "bad");
      }).catch(function (e) { show("Test hatası: " + (e.message || e), "bad"); });
    };

    el("aisave").onclick = function () {
      var body = {
        id: state.current && state.current.id,
        label: el("ailabel").value.trim(),
        base_url: el("aibase").value.trim(),
        model: el("aimodel").value.trim(),
        api_key: el("aikey").value.trim() || null,
        make_default: true,
      };
      if (!body.model) { show("Model adı zorunlu.", "bad"); return; }
      if (!body.base_url) { show("Base URL zorunlu (ör. https://api.openai.com/v1).", "bad"); return; }
      show("Kaydediliyor…", "");
      F1.fetchJSON("api/ai", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        if (r && r.ok) { render(r.state); show("Kaydedildi. Sonraki sorular bu sağlayıcıyı kullanır.", "ok"); }
        else show("Kaydedilemedi: " + ((r && r.reason) || "?"), "bad");
      }).catch(function (e) { show("Kaydetme hatası: " + (e.message || e), "bad"); });
    };

    el("aidel").onclick = function () {
      var p = state.current;
      if (!p || !p.id) { show("Silinecek profil yok.", "bad"); return; }
      show("Siliniyor…", "");
      F1.fetchJSON("api/ai?pid=" + p.id, { method: "DELETE" }).then(function (r) {
        if (r && r.ok) { render(r.state); fill(null); show("Silindi.", "ok"); }
        else show("Silinemedi.", "bad");
      }).catch(function (e) { show("Silme hatası: " + (e.message || e), "bad"); });
    };
  }


  // ---- 4b. kapsam çipleri + @ kısayol tamamlama ----
  /*
   * Scope is the ONE thing that decides which sessions an answer is based on, so
   * it is chosen by clicking, not by remembering syntax: a forgotten shortcut
   * silently answers from the wrong session. The @tokens remain as a power-user
   * shortcut and stay in sync with the chips, both ways.
   *
   * The chip list comes from the SERVER (`/api/scope?race=<id>`), which reads the
   * weekend ledger -- so the page can never offer a session the archive does not
   * have, and an empty session is shown as disabled with a reason instead of
   * being clicked and silently ignored.
   */
  function scopeChips() {
    var box = document.getElementById("chips");
    var input = document.getElementById("q");
    if (!box || !input || !input.dataset || !window.fetch) return;

    var picked = [];
    var owner = null;
    var sessions = [];

    function currentRace() {
      // The two surfaces name this differently: live sends "live", the archive
      // sends the race id. One lookup keeps both honest.
      if (typeof window.__scopeRace === "function") return window.__scopeRace();
      return input.dataset.scopeRace || "live";
    }

    function syncInput() {
      // Chips write @tokens into the box so the message itself says what it is
      // about; the server cleans them out before storing, so the transcript does
      // not accumulate stale scope.
      var text = input.value.replace(/(^|\s)@[A-Za-z0-9_]+/g, " ").replace(/\s{2,}/g, " ").trim();
      var prefix = picked.map(function (k) { return "@" + k.toLowerCase(); }).join(" ");
      input.value = (prefix ? prefix + " " : "") + text;
    }

    function render() {
      var chips = sessions.map(function (s) {
        var on = picked.indexOf(s.kind) >= 0;
        var isOwner = s.kind === owner;
        var cls = "chip" + (on ? " on" : "") + (s.has_data ? "" : " off");
        var title = s.has_data
          ? (isOwner ? "Bu sohbetin seansı" : "Bu seansı da bağlama ekle")
          : "Bu seans için kayıt yok";
        // Owner chips are not removable: a chat always belongs to its session.
        var clickable = s.has_data && !isOwner;
        return '<span class="' + cls + '" data-kind="' + s.kind + '" title="' + F1.esc(title) + '"'
          + (clickable ? "" : ' aria-disabled="true"') + ">" + F1.esc(s.label) + "</span>";
      });
      chips.push('<span class="chip sum" data-kind="__sum" title="Hafta sonu özetini bağlama ekle (tüm seanslar, tek blok)">Hafta sonu</span>');
      box.innerHTML = chips.join("");
      Array.prototype.forEach.call(box.querySelectorAll(".chip"), function (el) {
        var kind = el.getAttribute("data-kind");
        if (kind === "__sum") {
          el.onclick = function () {
            var cur = input.value;
            input.value = /@(all|w|weekend)\b/i.test(cur)
              ? cur.replace(/@(all|w|weekend)\b/gi, "").replace(/\s{2,}/g, " ").trim()
              : ("@all " + cur).trim();
            el.classList.toggle("on");
          };
          return;
        }
        var s = sessions.filter(function (x) { return x.kind === kind; })[0];
        if (!s || !s.has_data || s.kind === owner) return;
        el.onclick = function () {
          var i = picked.indexOf(kind);
          if (i >= 0) picked.splice(i, 1); else picked.push(kind);
          syncInput();
          render();
        };
      });
    }

    function load() {
      var race = currentRace();
      F1.fetchJSON("api/scope?race=" + encodeURIComponent(race)).then(function (d) {
        sessions = (d && d.sessions) || [];
        owner = (d && d.owner) || null;
        render();
      }).catch(function () { /* scope yoksa çip de yok: sessizce sade sohbet */ });
    }

    // Two-way sync: a token typed by hand lights up its chip.
    input.addEventListener("input", function () {
      var found = [];
      var re = /(^|\s)@([A-Za-z0-9_]+)/g, m;
      while ((m = re.exec(input.value))) found.push(m[2].toUpperCase());
      var next = [];
      sessions.forEach(function (s) {
        if (found.indexOf(s.kind) >= 0 && picked.indexOf(s.kind) < 0) next.push(s.kind);
      });
      if (next.length) { picked = picked.concat(next); render(); }
    });

    window.__scopePicked = function () { return picked.slice(); };
    load();
    // The archive changes race without reloading; keep the chips truthful.
    document.addEventListener("f1:race-changed", load);
  }


  // ---- 4c. sohbet akışı (SSE) ----
  /*
   * Why the chat uses a stream: the plain POST returned nothing for 30-110 s
   * while the model thought, and a proxy drops a byte-silent connection at
   * ~100 s -- answering with its own HTML error page, which the panel showed as
   * "HTTP 524 — sunucu JSON yerine HTML döndürdü". The answer existed (and was
   * paid for) but never arrived.
   *
   * `fetch()` + a manual reader is used rather than EventSource because
   * EventSource can only do GET and cannot send a JSON body (the question, the
   * scope, the lap). The parser below is deliberately tiny: our own server emits
   * exactly `data: <json>` lines and `: ping` comments.
   *
   * Callbacks:
   *   onScope(ev)          which sessions the answer is based on
   *   onThinking(chars)    reasoning is happening (count only, text not shown)
   *   onDelta(text)        append to the answer
   *   onDone(ev)           final answer + meta
   * Returns a promise that resolves on done/error and rejects on network failure.
   */
  function chatStream(url, payload, hooks) {
    var onScope = (hooks && hooks.onScope) || function () {};
    var onThinking = (hooks && hooks.onThinking) || function () {};
    var onDelta = (hooks && hooks.onDelta) || function () {};
    var onDone = (hooks && hooks.onDone) || function () {};
    var onError = (hooks && hooks.onError) || function () {};

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(payload),
    }).then(function (res) {
      if (!res.ok) {
        // A proxy answered instead of the surface. Say which, never paste HTML.
        return res.text().then(function (t) {
          var html = /^\s*<(!doctype|html|\?xml)/i.test(t || "");
          throw new Error("HTTP " + res.status + " — " +
            (html ? "vekil/katman HTML döndürdü (istek kesildi)" : (t || "").slice(0, 160)));
        });
      }
      if (!res.body || !res.body.getReader) {
        // No streaming support: the answer is not lost, but it cannot be shown
        // progressively. Fail loudly rather than silently showing nothing.
        throw new Error("tarayıcı akışı desteklemiyor (yanıt okunamadı)");
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buf = "";
      var got = null;

      function handleLine(line) {
        line = line.trim();
        if (!line || line.charAt(0) === ":") return;      // heartbeat
        if (line.indexOf("data:") !== 0) return;
        var body = line.slice(5).trim();
        if (!body) return;
        var ev;
        try { ev = JSON.parse(body); } catch (e) { return; }
        if (ev.type === "scope") onScope(ev);
        else if (ev.type === "start" || ev.type === "thinking") onThinking(ev);
        else if (ev.type === "delta") onDelta(ev.text || "");
        else if (ev.type === "done") { got = ev; onDone(ev); }
        else if (ev.type === "error") { got = ev; onError(ev.message || "bilinmeyen hata"); }
      }

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) {
            if (!got) onError("akış beklenmedik şekilde bitti");
            return null;
          }
          buf += decoder.decode(r.value, { stream: true });
          var i;
          while ((i = buf.indexOf("\n")) >= 0) {
            var line = buf.slice(0, i);
            buf = buf.slice(i + 1);
            handleLine(line);
          }
          return pump();
        });
      }
      return pump();
    });
  }

  // ---- 5. formatting helpers ----
  var F1 = {
    /* "3 dk" / "2 sa 5 dk" / "4 gün" -- coarsens as it grows, because a
       standings snapshot's exact second is noise. */
    ageText: function (seconds) {
      if (seconds == null || isNaN(seconds)) return "bilinmiyor";
      var s = Math.max(0, Math.round(Number(seconds)));
      if (s < 60) return s + " sn";
      var m = Math.round(s / 60);
      if (m < 60) return m + " dk";
      var h = Math.floor(m / 60), rm = m % 60;
      if (h < 24) return rm ? h + " sa " + rm + " dk" : h + " sa";
      var d = Math.floor(h / 24), rh = h % 24;
      return rh ? d + " gün " + rh + " sa" : d + " gün";
    },

    /* A timestamp the API/cache recorded, in local time. Anything unparseable
       renders as an em dash, never as "now". */
    whenText: function (iso) {
      if (!iso) return "—";
      var s = String(iso).trim();
      if (/Z$|[+-]\d\d:?\d\d$/.test(s)) {
        var d = new Date(s);
        if (!isNaN(d.getTime())) {
          return d.toLocaleString("tr-TR", {
            day: "2-digit", month: "2-digit", year: "numeric",
            hour: "2-digit", minute: "2-digit"
          });
        }
      }
      // No timezone: the cache writes UTC with a Z, so this is a fallback for
      // an older row. Show it as written rather than claiming a local offset.
      return s.replace("T", " ").replace("Z", "");
    },

    sign: function (n) {
      var v = Number(n) || 0;
      return (v > 0 ? "+" : "") + v;
    },

    surfaceUrl: surfaceUrl,
    /* Where a surface (plus an optional query string) lives, seen from here.
     * Exposed for surfaces that build links dynamically -- the welcome grid
     * links each session card to the archive with `?rid=`, which `rewriteNav()`
     * cannot do because it overwrites href wholesale. */
    surfaceHref: surfaceHref,
    rebindPorts: rewriteNav,
    chatStream: chatStream,

    /* Fetch a JSON endpoint and fail with a message a human can act on.
     *
     * The surfaces used to call `.then(x => x.json())` directly, so any non-JSON
     * answer surfaced as `SyntaxError: Unexpected token '<', "<!DOCTYPE"...` --
     * literally a proxy's HTML error page pasted into the chat window. That
     * tells the user nothing. The only thing worth showing is the status code
     * and the fact that a proxy answered instead of the surface, so say exactly
     * that and keep the HTML out of the UI.
     *
     * Resolves with the parsed body; rejects with an Error carrying `.status`. */
    fetchJSON: function (url, opts) {
      return fetch(url, opts).then(function (r) {
        if (r.ok) {
          // r.json() usually returns a promise, but a decode failure can also
          // throw synchronously -- and that throw carries the raw body in its
          // message (`Unexpected token '<', "<!DOCTYPE "...`). Wrap both paths
          // so the HTML never reaches the UI either way.
          var parsed;
          try {
            parsed = r.json();
          } catch (e) {
            throw new Error("HTTP " + r.status + " — cevap JSON değil");
          }
          return Promise.resolve(parsed).catch(function () {
            throw new Error("HTTP " + r.status + " — cevap JSON değil");
          });
        }
        return r.text().then(function (t) { return t; }, function () { return ""; })
          .then(function (body) {
            var html = /^\s*<(!doctype|html|\?xml)/i.test(body || "");
            var why = html
              ? "sunucu JSON yerine HTML döndürdü (vekil/katman hatası)"
              : (body || "").slice(0, 160).replace(/\s+/g, " ").trim() || r.statusText;
            var err = new Error("HTTP " + r.status + " — " + why);
            err.status = r.status;
            throw err;
          });
      });
    },

    /* Escapes text going into innerHTML. Every surface builds rows as strings,
       so this is the one place that has to be right. */
    esc: function (t) {
      return (t == null ? "" : String(t)).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    },

    /* Model answers are lightly marked-up: **bold** and `code`. Rendered as
     * HTML, so it MUST escape first -- the model's output is untrusted text
     * that lands in an innerHTML assignment.
     *
     * This lived only in index.html, so the archive surface printed the
     * asterisks literally ("**BEA** (Bearman)...") in both the transcript and
     * the panel's collapsed preview. Two copies of a formatter is how a bug
     * like that survives: the live page looked right, so nobody checked the
     * archive. One copy now, used by both. */
    fmt: function (t) {
      return F1.esc(t)
        .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
        .replace(/`(.+?)`/g, "<code>$1</code>")
        /* Headings and bullets.
         *
         * Measured on the archive surface: an answer that began
         * "## Bu yarış henüz bitmedi" reached the panel as literal hashes,
         * because only **bold** and `code` were handled. The model writes
         * Markdown headings and "- " lists unprompted, so the renderer has to
         * cover the two shapes it actually emits. Both are anchored to the
         * start of a line: "C#7" or "3 - 2" inside a sentence must not be
         * rewritten, and `^` with the m flag is what keeps them safe. */
        .replace(/^#{1,6}\s+(.+)$/gm, "<b>$1</b>")
        .replace(/^[-*]\s+(.+)$/gm, "\u2022 $1");
    },

    /* Plain-text version of the same string, for places that are NOT html:
     * the collapsed panel's preview strip uses textContent, so markup markers
     * would show through as literal asterisks there. */
    plain: function (t) {
      return (t == null ? "" : String(t))
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/`(.+?)`/g, "$1")
        // Must mirror fmt(): the collapsed panel's preview uses textContent, so a
        // heading marker left here shows up as literal "## " in the strip while
        // the panel body renders it as bold. Two renderers, one input.
        .replace(/^#{1,6}\s+(.+)$/gm, "$1");
    }
  };

  function boot() {
    rewriteNav();
    netBadge();
    registerSW();
    aiPanel();
    scopeChips();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  window.F1 = F1;
})();
