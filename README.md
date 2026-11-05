# F1 Agentic Analyst

**English** · [Türkçe](README.tr.md)

Three surfaces, one archive.

> **Your own key, on your own machine.** This project connects to no server, no telemetry or
> author infrastructure. You enter the AI provider and API key yourself; the key lives only
> in the `data/f1dash.db` file on your own machine (0600), never goes back to the browser
> and is never sent outside. There is no embedded provider — the installation is, until you
> configure it, completely inert as far as AI is concerned.
> Details: [AI provider selection](#ai-provider-selection), [SECURITY.md](SECURITY.md).

| Surface | Address | What for | Does it write? |
|---|---|---|---|
| **Live** (`server.py`) | http://127.0.0.1:8099 | During the race: running order, gaps, tyres, radio messages, AI chat, **provisional standings** | Yes — it records the feed |
| **Archive** (`archive.py`) | http://127.0.0.1:8100 | After the race is over: strategy strip, gap chart, lap-time distribution, chat | Chat only |
| **Standings** (`standings_app.py`) | http://127.0.0.1:8101 | The season's **official** driver and constructor standings; the last snapshot if there is no network | Standings cache only |

I separated the surfaces because the question being asked is different: in live it is "what is
happening right now" (a table is the right shape), in the archive it is "what happened, and
why" (a chart is the right shape), in standings it is "where is the season" (a ranking is the
right shape). **I did not separate the data** — two separate databases would have produced two
truths that could never reconcile with each other. All three use the `data/f1dash.db` file;
write authority is on the live side, the archive is read-only, and standings writes only to its
own cache tables.

The three surfaces share **one shell**: `static/theme.css` (tokens + nav + badges + notice
components) and `static/shell.js` (cross-surface links, formatters). Each page's own `<style>`
block carries only the rules specific to that page; tokens such as `--bg`, `--panel`, `--accent`
live in one place. The UI took its first shape from a design prototype; the prototype images
**are not kept in the repo** (they contained real race data), and only the CSS tokens remained.

## Files

```
server.py              live capture + the live surface's API (the only writer)
archive.py             archive browser (read-only + chat)
standings_app.py       championship standings surface (reads the cache, opens offline)
welcome_app.py         race-weekend surface: weekend picker + session cards
weekend.py             one weekend, one calculation: card grid + briefing summary
scope.py               scope resolver: which sessions enter the context (@shortcut + chips)
chat_request.py        the single chat pipeline — both surfaces call this
ai_config.py           AI provider profiles (base_url, model, key) + /api/ai
penalties.py           penalty/investigation parsing from race control (pure function)
store.py               shared database layer — all SQL lives here
analysis.py            shared analysis: MED8/TREND, sectors, provenance, LLM briefing
routing.py             resolves "past lap" / "another race" questions before the LLM
points.py              points tables + provisional-table projection (pure functions)
standings.py           Jolpica fetch (urllib), normalisation, SQLite cache
static/theme.css       shared theme: tokens, shell, nav, notice/strip
static/shell.js        shared shell behaviour + formatters + SW registration
static/sw.js           service worker (cache template; pwa.py fills it in)
static/icons/          PWA icon set (192/512/maskable/apple/favicon)
pwa.py                 PWA wiring shared by all four surfaces (manifest + /sw.js)
tools/make_icons.py    design/pwa-icon.svg -> static/icons/*.png
index.html             live surface
archive.html           archive surface
standings.html         standings surface
welcome.html           weekend surface (card grid)
tests/test_points.py   points arithmetic unit tests
tests/ui_test.js       browser-side behaviour tests (panel, keyboard, fetchJSON)
tests/layout_test.js   layout measurement in a real browser (chrome-headless-shell)
tests/test_pwa.py      manifest/precache/asset tests (no server needed)
tests/test_weekend.py  weekend book + card state + summary tests
tests/welcome_test.js  weekend surface behaviour tests (DOM mock)
tests/test_scope.py    scope resolution (@shortcuts, chips, ambiguity)
tests/test_ai_config.py provider profiles, no-secret-leak, precedence order
tests/test_stream.py   streamed chat: request body, meta parity, SSE parser
tests/test_penalties.py penalty parsing: withdrawn/investigation/multi-car distinction
tests/pwa_test.js      install + offline launch in a real browser
tools/shot.js          screenshot tool via CDP
tests/smoke_real_db.py smoke test against a copy of the real database
tools/backfill_openf1.py  complete a past race from the OpenF1 mirror
tools/audit_coverage.py briefing coverage audit: every stored field represented, nothing silently dropped
tools/fix_sector_laps.py repair sector rows stored against the wrong lap number
data/f1dash.db         the single SQLite file (WAL)
design/pwa-icon.svg    single source for the app icon (make_icons.py -> static/icons/)
```

No module repeats raw SQL: `server.py`, `archive.py`, `standings_app.py` and
`welcome_app.py` only
call `store.py` functions. The MED8/TREND calculation and the LLM briefing live in `analysis.py`,
the points arithmetic lives in a single place in `points.py`, so that the surfaces load the same
meaning onto the same data.

## Quick start

All you need is Python 3.10+ and three packages. There is no Node, no database server, no build
step — the SQLite file sets itself up on the first run.

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

Then enter your own provider from the **⚙ AI** button in the chat header:

| field | example |
|---|---|
| Base URL | `https://api.openai.com/v1`, `https://openrouter.ai/api/v1`, `http://127.0.0.1:11434/v1` |
| Model | `gpt-4o-mini`, `anthropic/claude-sonnet-4`, `llama3.1:8b` |
| API key | your own key — stored only on this machine |

All three values are required and **none of them is embedded in the code**: until you configure
it, the repo is inert as far as AI goes. If you are going to use a local model (Ollama,
llama.cpp, LM Studio), any value the provider accepts is enough in the key field; loopback is
allowed for `base_url`.

Tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'   # 203 test
node tests/ui_test.js                                          # 79 test
```

`tests/layout_test.js` and `tests/pwa_test.js` require a real headless Chrome; if it is
missing they are skipped (see the setup note at the top of `tests/layout_test.js`).

## Services

```bash
systemctl --user status f1-live f1-archive f1-standings f1-welcome
systemctl --user restart f1-live            # kod değişince
journalctl --user -u f1-live -f             # canlı log
```

`Restart=always` + `StartLimitIntervalSec=0`: if the process dies in the middle of a race (OOM,
network, an unexpected exception) it is back within 3 seconds and keeps retrying stubbornly.
Logs go to journald, and journald bounds disk usage itself — there is no separate logrotate.
Because `Linger=yes` is on, it comes up on a machine reboot without anyone logging in.

If you need to run it by hand: `./run.sh`, `./run.sh archive`, `./run.sh welcome`,
`./run.sh backfill --list`.

### Four surfaces, four ports

Each surface is a separate process on its own port, at the root path. There is
no path-prefix router in this build:

| Port | Surface |
|---|---|
| 8103 | weekend (session cards) — the landing page |
| 8099 | live timing |
| 8100 | archive (read-only + chat) |
| 8101 | standings |

They are separate processes on purpose: in one process the archive's CPU-bound
chart maths, or the standings surface's 15 s HTTP timeout, would share an event
loop with the loop reading the feed. Separate processes make that impossible.

`static/shell.js` is the only place that turns a port into a URL: every nav link
declares `data-port` and the shell substitutes the host the page was served
from, so the same markup works on `127.0.0.1` and on a LAN address.

## Mobile layout (<=640px)

All four surfaces carry phone rules; they all live in an inline `<style>` block on the
page, `static/theme.css` only carries the shared parts. (The weekend grid's mobile
rules are in `welcome.html`: two-column cards, ≥44px touch target.)

**Column hiding — with the reasoning for what goes:**

| page | total columns | remaining on phone | removed |
|---|---|---|---|
| live | 13 | 5 (P, DRV, GRID, GAP, TYRE) | TEAM, INT, LAP, LAST, BEST, MED8, TREND, PIT |
| archive | 15 | 9 (P, driver, gap, tyre, S1–S3, ideal, loss) | TEAM, INT, LAP, LAST, BEST, PIT |
| standings | 6 | 3 (position, driver, points) | TEAM, WINS, GAP |

On the live page the team name goes because the colour bar already sits next to the
driver. In the archive Last/Best go because Ideal and Loss summarise them
(`kayıp = gerçek - ideal`). In the standings the driver name wraps to two lines,
so it stays instead of the team name.

**Measured widths** (390px screen, ~360px usable; computed from character
widths, not verified in a browser):

- archive, 11 columns: ~673px → does not fit
- archive, 9 columns: ~469px → still does not fit, scrolls horizontally inside the card

```
canlı     5 kolon  → sığar
arşiv     9 kolon  → sığmaz, .card .body{overflow-x:auto} ile kaydırılır
```

**The height lock is gone.** `html,body{height:100%}` + `main{flex:1}` locked the page
to the visible area. Because the phone browser's address bar disappears on scroll, the
visible height keeps changing; the result is empty black space at the bottom and a page
you cannot scroll. On the phone it is `height:auto` + `min-height:100dvh`;
the table and the chat scroll inside themselves (the `min-height:100%` fallback stays
for browsers that do not know `dvh`).

**A column bug was fixed:** in the live table the header was `P DRV TEAM GRID GAP` while
the cells were in the order `pos, drv, team, gap, grid` — that is, under the `GRID` header
you saw the race gap, and under `GAP` the grid position. Now both
are `GRID → grid`, `GAP → gap`.

### Assistant: fixed bottom panel (sheet)

On the phone the chat is now a **fixed, collapsible** panel at the very bottom of the page.
On the live page it is `#chat`, in the archive `aside#chatpanel`. On desktop both are in
normal flow as before; this is entirely a `<=640px` rule.

```
kapalı : ┌──────────────────────────┐
         │ ▴ Asistan · son cevap…   │  46px şerit
         └──────────────────────────┘
açık   : ┌──────────────────────────┐
         │ mesajlar (kaydırır)      │  en fazla 78dvh
         │ [soru yaz…] [Sor]        │
         └──────────────────────────┘
```

**Why not a bubble (FAB):** the collapsed strip shows the first line of the last answer,
so you can ask the question, close the panel, watch the table, and the answer shows up
in the strip when it arrives. A bubble collapsed is just an icon — the "did the answer
arrive" information is lost. Also, the 56px circle in the bottom right on a phone would
land exactly on top of the `GAP`/`INT` columns.

**Keyboard (a real Android phone):** the panel stayed behind the keyboard, a two-layer
fix:

1. `<meta name="viewport" … interactive-widget=resizes-content>` — when the keyboard
   opens the layout viewport shrinks, and `bottom:0` sits on top of the keyboard.
   Because the default (`overlays-content`) overlays the keyboard on the page, the
   panel stayed exactly behind it.
2. A `visualViewport` fallback — for browsers that do not support the key,
   `bindKeyboard()` measures the visible-area difference and pushes the panel up by that
   much (`innerHeight - vv.height - vv.offsetTop`). The 80px threshold keeps the
   address-bar jitter from being mistaken for a keyboard.

The text field is `font-size:16px` so iOS/Samsung do not zoom the page on focus.

**The height is given EXPLICITLY with `height` — `max-height` is not enough.** Two separate
bugs came out of this, both seen on the phone:

1. The first version did the collapsed state with `translateY(calc(100% - 46px))` and
   **more** than 46px of content was visible. A `%` transform is computed against the
   element's own box, and the box's height varies with the content; the difference stayed
   on screen.
2. Then `max-height:46px` was written but the panel **never collapsed at all**. The reason:
   `#chat{min-height:58dvh}` — a rule added while the chat was a column in flow, and
   forgotten when the panel was made fixed. **In CSS `min-height` beats
   `max-height`.** The rule was deleted, replaced by `height:78dvh` /
   `.collapsed{height:46px}`.

`height` is also required for `#msgs{flex:1}`: a flex child only stretches if the container
has a definite height. With only `max-height` the panel would be as tall as its content,
the message area would not grow, and the space would be wasted — that was the
"the panel does not sit on the page" look.

`tests/ui_test.js` section 7 checks this through the text: if the panel has a non-zero
`min-height`, or if `.collapsed` uses only `max-height`, the test breaks. Without a
browser this kind of precedence bug is not visible any other way.

**`pointer-events:none` when collapsed** — everything outside the handle is unclickable,
otherwise with the keyboard open you could tap the form behind it.

**When open the handle is just a grab bar.** The label and preview are hidden:
the handle read "ASİSTAN" and right below it a tab read "ASİSTAN", a double label.

**No duplicate heading.** The handle reads "Asistan" / "Arşiv sohbeti"; right below it the
same heading was repeated. On the phone `#chathead .t` and in the archive
`.chathead .ttl` are hidden, the buttons (thinking/clear/preference) stay.

**Handle `flex:0 0 46px`.** `height:46px` alone is not enough: `#chat` is a flex
column and collapsed its total height is 46px; the handle with `flex-shrink:1` was crushed
to 12px and the content below showed through in its place (measured: 12px).

**Panel 62%, 100% with the keyboard open.** At 78% the table was fully covered
(measured 713/915px). The panel's job is not to hide the table but to stand beside it.
When the `visualViewport` shrinks (keyboard opened) `height` is set equal to the visible
area — so you see the answer while typing.

**`chathead` single line.** With `flex-wrap:wrap` three items did not fit in 412px and
`temizle` dropped to the next line, making the header 74px (measured). Now `nowrap`;
the `#pref` text truncates with an ellipsis at 108px.

**`[hidden] { display:none !important }`** — in `static/theme.css`. The browser default has
very low specificity (0,0,0); `.subtabs{display:flex}` overrode it and left the `hidden`
element visible. That is why on the phone the tab bar stayed on screen even though the
tabs had been removed.

### Provisional standings outside the panel on the phone

On the live page "Geçici puan durumu" (provisional standings) was a sub-tab inside the panel. On
the phone that meant two things: the 30px tab bar directly ate into the message area, and
the tab logic made no sense — the chat is already in a fixed panel, while the provisional table
is a summary you glance at now and then.

`hoistProv()` (below 640px) pulls the `#pane-points` node out of the panel and moves it to
`#provslot`: below Race Control, in the page flow, always open.
The tab bar is hidden. On desktop nothing changes (the tabbed version is correct).

**The DOM is moved, not copied:** `renderProvisional()` keeps writing to a single `#plist`,
so there is no risk of keeping two copies.

### The two chat panels are exactly the same

The live and archive pages' chat block is **in the same order, at the same measurements**:

```
head (başlık · düşünme · tercih · temizle)
msgs
chips          (öneri soruları)
usage          (token/kredi şeridi)
form           (textarea + Sor)
srcline        (Veri: tur N · …)
```

The order is not a coincidence. In the archive `düşünme` was a separate `.lvl` block
**above** the form, on live it was inside `chathead`; that is why on the two pages the form
started at different heights. The comparison was done by measuring (1280px and 412px):

| area | target | measured (same on both) |
|---|---|---|
| chat column | equal width | 453px @1280 · 412px @mobile |
| chips bottom edge | same | 778 |
| usage | same | 20px @778 |
| form | same | 69px @798 |
| srcline | same | 33px @867 |

**The column ratio was equalised too.** The archive was `1fr : 0.42fr` (379px at 1280px),
live `1.55fr : 0.85fr` (453px). In the narrow column the same usage strip dropped to 2 lines,
became 45px instead of 20px, and everything below it shifted down 25px.
So the precondition for the "make them parallel" request was equalising the width.

**Suggestion chips came to the archive too.** Questions like "Şu an en hızlı kim" (who is fastest right now) from live
are meaningless in the archive; four archive-appropriate questions were written ("Bu turda en hızlı kim",
"İkinci stint neden kötüydü"…). The class name is `.schip`, because `.chip` is already used on this page
by the **driver-selection pills** (`#gapchips`) — giving it the same name
would break those pills' styling.

**Chips were removed on the phone** (2026-10-31). In the fixed bottom panel the four chips
wrapped to two lines and ate ~60px; in the panel's 62% the chat itself was
barely visible (seen in a screenshot). Inside `@media
(max-width:640px)` the live `.chips` and the archive `.schips` only get
`display:none` — the HTML and click logic stay in place, and on desktop the
chips remain (measured: at 1280px `.chips` is still `display:flex`).

### "kaynak live" was being misread

At the bottom of the archive page it said `Veri: tur 56 · kaynak live`. `snap.source`
says which **writer** produced the frame (`live` = live capturer,
`openf1` = community mirror) — it does not mean "the data is live right now". The page
was showing a read-only archive record, yet the label said "live" (canlı).

Now both are written:

```
arşiv : Veri: tur 56 · arşiv kaydı (canlı yakalama)
canlı : Veri: tur 56 · canlı yayın · feed 20:30:44Z
```

Nothing made up: the mapping turns two real values into two real names.

**`#srcline` was added to the live page.** The same information was there inside `#stamp`, but
`#stamp` is in the header and `.head-info.optional` is hidden below 640px — meaning on mobile
it never said which data you were talking to. On both pages it is below the chat.

### Markdown in one place

Model answers arrive lightly marked up: `**bold**` and `` `code` ``. The formatter
was for a long time **only in `index.html`**; the archive used `esc()` and
`**BEA** (Bearman)` showed up with its markers. Because the live page looked right
nobody had looked at the archive — that is the price of keeping two copies.

Now there are two functions in `static/shell.js`:

- `F1.fmt(t)` — for `innerHTML`, **escapes first** then formats.
  The model's output is untrusted text; if the order were reversed it would be XSS.
- `F1.plain(t)` — for places written with `textContent` (the panel strip), strips the same
  markers.

**Headings and bullets are here too.** A real archive answer began with `## Bu yarış henüz
bitmedi` and landed in the panel as raw hashes: the formatter only knew
`**bold**` and `` `code` ``, while the model spontaneously writes Markdown headings and
`- ` lists. `fmt()` now also converts leading `#{1,6}` and `[-*]` markers;
thanks to the `^` + `m` flags, "C#7" or "3 - 2" are not mangled.
`plain()` strips the same markers, because the collapsed panel's strip uses
`textContent` — two renderers, one input.

## PWA — all four surfaces are installable apps

All four pages are PWAs now: when you say "Add to Home Screen" on the phone, what gets
installed is not a browser shortcut but an app that opens **in its own window with no
address bar**, and it opens offline too.

Four separate apps get installed (live, archive, standings, weekend), not one
app: the four answer different questions at different times. With a single icon, a shortcut
installed for the standings in the off-season would drop into the live timing table on
every launch; and a weekend shortcut opened on a Tuesday would land on an empty
table.

### The cache name derives from file contents (no manual bump)

`CACHE_VERSION` was constant (`"f1-shell-v1"`) with a comment next to it saying "bump this when
a cached file changes". Nobody bumped it: the page, `theme.css` and
`shell.js` changed but **the worker's own bytes did not change**, so the browser never
installed the new worker and the returning user was served the old page + old JS.
The user's report was exactly this: *"değişiklik yok gibi"* (like there is no change).

Now the name is **the hash of the cached bytes** (`f1-shell-<12 hex>`), including the
surface's own page. Any edit changes the name → `/sw.js` changes → the browser
installs the new worker → `activate` deletes the old cache. There is no step to remember.
The four surfaces get separate names, otherwise two installed apps would share the same cache and
delete each other. `tests/test_pwa.py` checks that the name is derived, that it differs by
surface, and that it stays stable for unchanged files.

**Lesson:** a comment saying "do this when you release" is a step that does not get done.

### Wiring in one place: `pwa.py`

`server.py`, `archive.py`, `standings_app.py` and `welcome_app.py` all make the same
`pwa.install(app, yüzey)` call. Each surface produces its own `/manifest.webmanifest` and
`/sw.js` response; both are on the **root** path, not under `/static` — a service
worker's scope is its own directory, so `/static/sw.js` would only control `/static/`
and would never see any page navigation.

| file | what it serves |
|---|---|
| `/manifest.webmanifest` | surface-specific name/short name, `display: standalone`, 192+512+maskable icons |
| `/sw.js` | the `static/sw.js` template; cache name and precache list filled in from Python |
| `/favicon.ico` | 32px icon (the browser also asks for it at the root) |
| `/static/icons/*` | the icon set, from the existing `/static` mount |

### Why `start_url` is `"./"`

Each surface is reached on its own port at the root path (`http://127.0.0.1:8099/`),
so `start_url: "/"` would work today. It is still `"./"`, because it costs
nothing to be correct: `"/"` is absolute and breaks the moment the app is served
under any prefix. Per the manifest standard, `start_url` and icon `src` paths are
**resolved against the manifest's own address**; `"./"` means "whatever directory
this manifest came from". The surfaces then never need to know their own address.

### Cache strategy (`static/sw.js`)

| request | behaviour | why |
|---|---|---|
| page navigation (`navigate`) | network first, cache if that fails | the shell opens instantly but does not hide a new version |
| `GET /api/...` | network first, cache if that fails | a stale snapshot is **honest**: every surface writes its own timestamp, `shell.js` shows the offline badge |
| other GET (css/js/icon/manifest) | stale-while-revalidate | the installed app's "opens instantly" feel comes from these |
| `POST`/`DELETE` | network only | chat/settings/refresh is a write; a "success" the server never saw is worse than an error |
| `/api/stream` (SSE) | never touched | caching a response that never ends locks up the Cache API |

The precache list is all or nothing: `cache.addAll` rejects on a single 404, and a half-finished
cache is the worst case (it looks present, then blows up on the file you need).
So if installation fails the worker is not installed at all — visible in DevTools.

`CACHE_VERSION` (`pwa.py`) is a single-letter version stamp: `activate` deletes every cache whose name it does not
hold, so bumping the version performs the eviction.

### A `/api/state` fallback on the live page

The live surface used to get its data only over `EventSource` (`/api/stream`).
If the stream could not be established — offline, or a proxy that cuts SSE — the screen sat on
"veri bekleniyor..." (waiting for data) forever, even though the worker's cache had a fresh frame.
Now `es.onerror` and page load also call `/api/state` (that endpoint
is cached as `GET /api/...`). Because the frame is **old**, the page shows its own
timestamp and the offline badge; there is no fabricated "now".

Since the stream and the fallback call race, the guard is on the `updated` field: ISO and time-zone
stamped. `server_time` is a time of day ("20:43:47") and in a session crossing midnight
a string comparison would pick the wrong frame.

### The offline badge

`shell.js` puts a `#netbadge` with class `.badge.warn` in the header ("çevrimdışı" — offline)
and turns it on and off on `online`/`offline` events. No new CSS. The badge is necessary
because the worker serves the shell **and** the last API snapshot from the cache: without a badge
an offline page would be indistinguishable from a live page with yesterday's numbers.

### HTTPS requirement

`navigator.serviceWorker.register()` only works in a **secure context**:

| access | PWA | reason |
|---|---|---|
| `http://127.0.0.1:8099` | ✅ | localhost counts as secure |
| `http://192.0.2.10:8099` | ❌ (plain page) | plain HTTP, not a secure context |
| `https://your-host/...` | ✅ | TLS, however you terminate it |

`shell.js` checks this with `isSecureContext` and silently skips registration:
there is no point in seeing a console error on every load of a page opened over a
plain-HTTP LAN address. The page itself still works; only installing does not.

### Icons

`design/pwa-icon.svg` is the single source; `tools/make_icons.py` produces five PNGs
(192, 512, maskable-512, apple-touch-180, favicon-32). This machine has no ImageMagick and no
librsvg, and snap Chromium does not run because of `snap-confine`; rasterisation is done with
`chrome-headless-shell`, which the tests already use.

The maskable variant is produced separately: Android crops the maskable icon to a circle
covering the central 80% of the square, so if the normal artwork were declared as maskable
the two ends of the gauge would be eaten on the phone home screen.

### What is tested

- `tests/test_pwa.py` — serverless: does every file in the precache exist, is `start_url`
  relative, are the install criteria complete (192+512+maskable, standalone), is the filled-in
  `sw.js` valid JS, are SSE and writes skipped, does each of the four pages' `<head>`
  link the manifest, do `theme_color` and `--bg` not diverge, is there really a page served
  behind every manifest.
- `tests/pwa_test.js` — in a real browser: the worker registers and its scope covers the page,
  the shell+icons are in the cache, when **the network is cut** and the page is refreshed
  the shell comes from the cache and the badge appears, and the archive and weekend surfaces
  get their own worker + manifests.

## Running it locally only

This build is **loopback-only by design**. There is no tunnel, no mesh VPN, no
reverse-proxy integration and no path-prefix router in the repository: the four
surfaces bind to `127.0.0.1` and are reached at their own ports.

| Surface | URL |
|---|---|
| Weekend | http://127.0.0.1:8103/ |
| Live | http://127.0.0.1:8099/ |
| Archive | http://127.0.0.1:8100/ |
| Standings | http://127.0.0.1:8101/ |

That is deliberate, and it is a security decision as much as a scope one: **the
app has no authentication.** Every request it serves is treated as coming from
someone allowed to see the archive, change the AI provider and spend API credit.
Binding to loopback is what makes that safe. If you widen the bind address with
`F1_HOST=0.0.0.0`, you are handing those powers to everyone who can reach the
port.

### If you want to reach it from another machine

You can, and it is your call — but understand what you are exposing, and put
authentication in front of it yourself. Two shapes that keep the app unchanged:

- **A reverse proxy with auth** (Caddy, nginx, Traefik) in front of each port,
  terminating TLS. Do not expose a port without the auth layer.
- **A private network** (Tailscale, WireGuard, an SSH tunnel) so the port is
  reachable only from your own devices.

Neither is configured here. Nothing in this repository assumes one, and no code
path depends on being behind a proxy — every URL is relative to the origin it
was served from, so a proxy that keeps paths intact works without changes.

### Why the app still sends heartbeats

`/api/stream` and `/api/chat/stream` write a comment line every few seconds
while they have nothing to say. On loopback that is close to pointless — but it
costs a few bytes and it is the difference between working and hanging the day
someone puts any byte-buffering intermediary in front of it. The mechanism is
kept; the assumption that there always is one is not.


## Adding a past race to the archive

F1's own static archive is now behind an auth wall (every `*.jsonStream` request gets
401), so a race that wasn't captured live is lost as far as the official feed is
concerned. The OpenF1 community mirror keeps an independent copy and is rich enough to
rebuild one lap of frames.

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

`--race-id`/`--all` fill **existing** race records; a race that was never captured live
has no `races` row, which is why `--ingest-season` was added. That mode reads the
season's Race sessions from OpenF1, skips the ones with `is_cancelled` and races whose
`date_start` hasn't passed, opens records for the ones not in the archive in the same
format as the live writer (`2026/2026-03-08_Australian_Grand_Prix/2026-03-08_Race/`),
takes the name from the Jolpica calendar (OpenF1 knows the venue, not the race name; if
there is no calendar it writes `<ülke> Grand Prix` and says so), takes the race distance
from **the last lap that has a duration**, and then runs the normal phases. If backfill
opened the record, `races.source='openf1'` — it doesn't look like a live capture.

One caveat: the mirror isn't complete for every race. In 2026 Australia OpenF1 returns
only a few laps per driver (20 lap times, 8 frames); that race stays thin in the archive
and isn't padded from another source. In Belgium the lap-45 record has no duration
either, so the race distance is counted as 44 laps.

Matching is done by **`meeting_key`** — F1's `SessionInfo.Meeting.Key` value uses the
same numbering as OpenF1's `meeting_key`, so there's no date/lap guessing involved.

### Provenance (source label)

Every lap row carries two extra fields:

- `frames.source` — `live` (recorded from the live feed) or `openf1` (rebuilt from the archive)
- `frames.missing` — list of fields this source can't provide (e.g. `["grid"]`)

This isn't decoration. OpenF1 is **not** a superset of live: live has sector segments,
~0.27 s intra-lap resolution and the full race-control stream; OpenF1 may lack those but
also has laps/gaps we missed. Without the `missing` list, archive chat invents sector
data that doesn't exist — the "no fabrication" rule breaks exactly here. Therefore:

- `analysis.context_text()` adds `KAYNAK:` and `EKSİK ALAN KURALI:` lines to the briefing,
- the archive UI shows the "eksik alan: …" [missing field: …] badge in the header and prints `—` in the table,
- unless `--overwrite` is given, backfill **never** writes over a live row
  (`INSERT OR IGNORE`): live data is richer, so it wins.

The starting grid doesn't exist in OpenF1 but is constant for the whole race; if it's in
the same race's live frames it's inherited (`store.all_row_grids`), otherwise it goes
into `missing`.

## Standings: the official table and the provisional table

Two separate questions, two separate tables, **one points arithmetic** (`points.py`):

| | Where | What it shows | Source |
|---|---|---|---|
| **Official standings** | Standings page (:8101) | The season's finalized driver/constructor points | Jolpica API → SQLite cache |
| **Provisional table** | Live page (:8099), "Geçici puan durumu" [provisional standings] tab | What the table would be if the race *finished in the current order* | Base (cache) + live order |

### Points table in one place

In `points.py`, `RACE_POINTS = (25,18,15,12,10,8,6,4,2,1)` and
`SPRINT_POINTS = (8,7,6,5,4,3,2,1)`. No other module repeats these numbers; the live
panel, the standings page and the tests all call the same function. A second copy isn't
a "style issue" — copies drift apart and two surfaces on the same screen say different
things about the same number.

Deliberately **not done**:

- **No fastest lap point.** It existed until 2024 and was dropped in 2025. Keeping it in
  the table would mean inventing a point that no longer exists. If it comes back it comes
  as a table entry, not as a bonus buried in the code.
- **No half points.** The live feed can't say in advance that the race will be shortened;
  a halved table would be a guess. The UI says "tam puan varsayımı" [full-points assumption] instead.
- **No tie-break.** On equal points the official order is a count of best results across
  all the season's results — data we don't have. So equal totals are marked `tie` and
  **share the same rank**; the order on screen isn't presented as the official order.

### Running offline: cache + staleness

The standings page exists so it can be opened without a network. That's why the read and
write paths are separate:

- **Read** (`standings.get_standings_view`) never touches the network; it reads from SQLite.
  Alongside it returns `fetched_at`, `age_seconds`, `stale` and, if there is one, the error
  from the last failed attempt.
- **Write** (`standings.refresh`) fetches from the API, normalizes, writes to the cache.
  A failed attempt is written to the `standings_attempts` table too — it doesn't stay silent.

The page states the three states **separately**:

1. **Fresh:** the table looks normal, the strip shows the source + the age of the last update.
2. **Stale:** yellow warning — "son çekilen anlık görüntü gösteriliyor (tarih, yaş önce).
   Aradan geçen yarışlar burada eksik." [showing the last fetched snapshot (date, age ago). Races since then are missing here.]
   The missing rank is **not filled with zeros**.
3. **No data at all:** red warning — "bu sezon için kayıtlı sıralama yok, puan tablosu
   uydurulmuyor" [there is no recorded standings for this season, the points table is not being made up], plus the last error text if there is one.

Cache freshness is 6 hours (`standings.FRESH_FOR`); after a failed fetch it isn't retried
for 2 minutes (`RETRY_AFTER`) — on a dead network, waiting for a DNS timeout every time
the page opened would make the page unusable.

The source address can be changed with `F1_STANDINGS_API` (for mirror/DNS changes or
offline tests). Default: `https://api.jolpi.ca/ergast/f1`.

### Provisional table: "if the race finished in this order"

The tab on the live page renders the output of `points.provisional()`:

```
taban puanlar (bu yarış hariç)  +  sürücünün MEVCUT sırasının puanı  =  geçici toplam
```

Each row has the base points (faded), this race's contribution (`+25` green, `—` for no
points) and the provisional total. When the order changes the table updates itself on
the next SSE frame — no separate fetch, no extra cost. How many places were gained or
lost relative to the base order shows as `▲2` / `▼1`; **an unchanged row carries no
marker** (printing a dash would read as "lost nothing").

**Double-counting protection.** This is the one place where the projection can lie: if
the base in the cache already includes the race being shown, the points are counted
twice. `standings.base_rows_for_projection()` checks this in two ways and, if it's
suspicious, **rejects the projection**:

- the round number found from the calendar: reject if `taban_round >= yarış_round`;
- if there's no calendar, the date: reject if `fetched_at >= yarış tarihi`.

When rejected the panel doesn't draw a table, it writes the reason. If the session has
ended the message changes: "seans bitti — resmî sıralama artık kesinleşti, güncel tablo
Puan Durumu sayfasında" [session over — the official standings are final now, the current table is on the Standings page].

A driver who is **not** in the base table (e.g. a mid-season change, a stale cache)
scores points but isn't added to the table; their name is listed separately as "bu
yarışta sırada ama taban tabloda yok" [in the running order in this race but not in the base table]. Whose row it is
isn't invented.

### Calendar

The `standings_snapshots` table also holds the season calendar with `kind='schedule'`.
Its single purpose: to answer "which round is the race currently shown" **without
guessing**. Matching is done by calendar date; if two races land on the same day it's
narrowed by name, and if that fails too it returns `None` (no guessing).

## Cost

Measured: RSS ~62 MB, live capture about 0.02% of one core. A lap frame ~12.9 KB →
~0.8 MB per race; a sector row 132 B → ~0.18 MB per race. Total per season
~23 MB. A chat message ~1 KB. Backfill writes in short transactions (12 frames /
500 lap timeout) and waits with `busy_timeout=15000`, so it never locks live
capture during a race.

Sector statistics are computed inside `snapshot()` every second, which is why the
best-sector scan is guarded with a 5-second cache: cold 15.4 ms, warm 0.7 µs.
The `/api/state` response stays between 4-24 ms.

## Sectors and mini-sectors

Sector times (`S1/S2/S3`) are real data and live in three places: the live feed
(`TimingData.Sectors`), OpenF1 (`duration_sector_1..3`, populated in 99% of laps) and
therefore in the `sectors` table in the database.

**Mini-sector times are in no source at all.** Neither the live feed nor OpenF1 publishes the
*duration* of a mini-sector; they only give each one's status code (`2048` personal best,
`2049` session best, `2051` no improvement). These codes are stored as CSV
in the `seg1..seg3` columns — as evidence, without inventing meanings. The sentence
"lost 80 ms in mini-sector 2" cannot be constructed from any data; so no such answer is
given in the archive.

Sectors are not embedded in `frames.snap`, they sit in a separate table. If they were embedded, the lap frame
would grow from 12.9 KB to 31.4 KB (sectors are 56% of the frame) — that is, 42 MB per season
instead of 23 MB, and as unqueryable JSON on top. In a separate table every lap becomes three numbers and one
lookup index.

Two derived numbers make sectors answerable:

- **Ideal** — the sum of the driver's own best sectors; the lap they
  would have done if they could repeat all of them.
- **Loss** — actual lap time minus ideal; the time spent.

Both come from the same `analysis._sector_stats` function on both surfaces, so the live
agent and the archive see the same number. In the archive table the race's fastest sector is marked
purple; an unreported sector stays `—`, neither zero nor a guess is printed.

## Briefing coverage: is every stored field written down

The most expensive error class so far has been the **silent gap**: the data
was in the database, it did not get into the briefing, and the agent honestly said "that data is not in the feed".
Sectors, per-lap position, calendar date, full names, the RETIRED flag and pit lane
laps were discovered this way, each through a separate user question.

The reason is structural: the agent only sees the **text** produced by `context_text()`, it does not see the
`frames.snap` schema. It cannot inventory what it does not have — it cannot see the negative space.
And the error was silent: a fabrication leaves an inconsistency, whereas an answer born of a missing
premise comes out perfectly consistent.

`tools/audit_coverage.py` does not leave this to the user:

```bash
.venv/bin/python tools/audit_coverage.py --strict   # eksik varsa exit 1
```

The audit is **two-way**:

1. **Stored → briefing.** `frames.snap`, row fields, the `races` table and the auxiliary
   tables (`sectors`, `lap_speeds`, `lap_times`) are read; every field falls into one of three boxes:
   **represented** / **deliberately left out** (with its justification) / **undecided**.
2. **Source → stored.** Every field in the OpenF1 `/laps` response must either be in the database
   or be left out with a justification.

The second direction was the blind spot of the first: `i1_speed`/`i2_speed`/`st_speed` were in
every response but were never stored — the "stored ↔ briefing" audit could not see this, because the
field was never on the "stored" side to begin with. This gap surfaced only when the user
asked "you said mini-sectors are in OpenF1".

Both directions were verified: adding an invented field to the schema and deleting a field from
`OPENF1_LAPS` makes the script catch it and exit 1. For offline use, `--no-network`.

| Field | Why it is left out |
|---|---|
| `color` | UI colour only |
| `num` | already appears as `CAR 12 (ANT)` in RC messages |
| `updated` | empty in the feed; time information is in `server_time` and RC timestamps |
| `stints` | the `TÜM TURLAR` [ALL LAPS] block already writes the ranges (`[SOFT 1-15]`) |
| `is_pit_out_lap` | the PIT column + the `PIT LANE TURLARI` [PIT LANE LAPS] row give the same information |
| `date_start` | lap start time; there is no in-report simulation |
| `meeting_key`, `path`, `id` | matching/identity, internal use |

Added in this round (briefing 19.5k → 20.7k characters, +6%):

| Block | What becomes askable | Before |
|---|---|---|
| `YARIŞ KİMLİĞİ` [RACE ID] | "when was this race" | ❌ "there is no date in the data" (whereas `races.start_date` is populated) |
| `SÜRÜCÜLER` [DRIVERS] | "where is Hamilton" — code↔name mapping | ⚠️ was left to the model's world knowledge |
| `YARIŞ DIŞI` [RETIRED] | "why did he retire" | ❌ "COL did not retire" — it contradicted itself |
| `PİT LANE TURLARI` [PIT LANE LAPS] | "on which lap did he pit" | ⚠️ required inference from the stint range |

The `YARIŞ DIŞI` line deliberately says "the reason is not in this flag": RETIRED only shows
that he withdrew, not why. We draw the boundary in the text so the model does not make it up.

## Mini-sectors: the code is there, the duration is not

Mini-sector **status codes** exist in both sources and are stored
(`sectors.seg1..seg3`, CSV):

| code | meaning |
|---|---|
| `2048` | personal best (P) |
| `2049` | session best (S) |
| `2051` | no improvement (.) |

Mini-sector **duration** is in no source — neither in the live feed nor in OpenF1. That is why
in the `EN İYİ TUR` [BEST LAP] block it is written as a flag string (`?P.SP./.SPPPPP./P.SSSPPS`), it is not
summed like a duration, and no "lost 80 ms there" sentence is constructed. The flag string answers the
question "who was purple where"; the question "how many ms" is answered by sector times.

An empty field holds its place as `?`, it is not skipped — if it were skipped, the grids of two drivers would shift and
positions would be silently misaligned.

### Mini-sector strips (in the table)

In the live table and in the archive, **under** each sector time sits a thin strip: that
sector's mini-sectors. The strip shows a **status, not a duration**, which is why all
bars are of equal width — length means nothing:

| colour | code | meaning |
|---|---|---|
| purple | `2049` | session best |
| green | `2048` | personal best |
| yellow | `2051` | no improvement |
| transparent | `0` | no data (the feed queue pads with zeros); its place is kept, it is not painted |
| faint grey | other | a code not in the mapping (`2064`, `2050`, `2052` were seen in the archive); no meaning is invented |

It speaks the **same language** as the sector colour (purple = fastest) but is not the same
measurement: the sector colour compares *durations*, the strip shows *mini-sector codes*. If there are no codes
at all, the strip is not drawn — an empty grey strip would read as "everything is no improvement".

The strip comes from the **same lap** as the sector times next to it: `row.segs` is the one for the
lap on screen, `row.b_segs` the one for the best lap (`analysis._sector_stats`). Pairing two different laps
in one cell would be a silent lie, hence the separate fields.

## Speed points (`i1`, `i2`, `st`)

OpenF1 gives three real speed measurements per lap: `i1_speed` (end of S1), `i2_speed`
(end of S2), `st_speed` (speed trap). These **were in every `/laps` response and were never
stored** — sector comparison could only be done in seconds.

```bash
./run.sh backfill --race-id 1 --phase speeds   # 1164 satir, 1020'si uc hizi da dolu
```

They live in the `lap_speeds` table (`num, lap, i1, i2, st, source`), and `put_speeds`
merges them with COALESCE: a partial write does not delete the previous value.

The `EN İYİ TUR` [BEST LAP] block gives, per driver, the fastest lap, **that** lap's own sector times,
the three speeds and the mini-sector flags:

```
LEC | tur 42 97.976sn S=26.289/38.906/32.781 hiz i1/i2/st=215/171/310 mini=?P.SP./.SPPPPP./P.SSSPPS
HAM | tur 48 98.457sn S=26.076/39.274/33.107 hiz i1/i2/st=213/166/310 mini=?PPPPP/PPPSPPPP/PSPP.SPP
```

The `S=` field is mandatory: the sectors in the POS table are those of the **current** lap. Before this field
was added the model matched the best lap time with another lap's sectors
(it gave LEC's lap 42 S1 as 26.164 — whereas it is 26.289; 26.164 was lap 40). The block
header and the SYSTEM rule explicitly forbid this mix-up.

## Per-lap ranking

Every lap frame stores the `POS` column, so the answer to "what position was LEC in on lap 15" **was
there** in the database. Because it did not get into the briefing, the agent said "that data is not in the feed" — this was
not missing data, it was a wrong answer. Now the `TUR BAZLI SIRALAMA` [PER-LAP RANKING] block is in every briefing.

Instead of one number per lap, the block writes a **range**:

```
LEC | 1-13:3 14-15:2 16:6 17-19:5 20-24:4 25-39:2 40-46:4 47-48:3 49-56:2
```

Three encodings were measured (this race, 22 drivers):

| encoding | size | problem |
|---|---|---|
| full series (`3 3 3 3 …`) | 3149 characters | readable but +18% of the briefing |
| change point (`14:2 16:6 …`) | 1852 characters | **requires inference** |
| **range** (`14-15:2 16:6 …`) | **2116 characters** | no inference |

Change points were the smallest, but in the test the model, on the question "what position on lap 30", first said "4th"
and then corrected itself (the correct answer is P2). The range encoding costs 264 characters more and
removes the inference entirely: lap 30 → `25-39:2` is read directly. Briefing 17.1k → 19.5k
characters (+14%).

Three questions were verified against the raw `frames` table:

| question | answer | raw data |
|---|---|---|
| "what position did LEC finish stint 1 in" | P2 | lap 15 → pos 2 ✓ |
| "what position was LEC in on lap 30" | P2 | lap 30 → pos 2 ✓ |
| "when did VER drop to 3rd" | lap 49 (47-48 P2) | laps 47/48 → 2, lap 49 → 3 ✓ |

The block is guarded with a 5-second cache: setup 110 ms (parsing 56 frames), a warm
call is free.

## Weekend surface and weekend summary

The fourth surface (`welcome_app.py`, 8103) shows not a race but **which sessions
exist**: you pick a weekend, that weekend's sessions are drawn as cards. Card state is one of
four values, and all of them come from **capture evidence**, not from the
clock:

| State | What it means |
|---|---|
| `live` | `last_seen` within the last 90 s and the session not finished (`server.touch_race` every 15 s) |
| `done` | frame/lap records exist, the session has finished or is stale |
| `scheduled` | no records at all **and** `start_date` in the future |
| `empty` | no records at all, `start_date` in the past (capture missed it) |
| `missing` | that session has no row in the weekend at all |

The `scheduled` / `empty` distinction matters: both say "no data", but one means "not yet",
the other "we missed it". And **a future `start_date` alone is not enough**:
in the real archive the Mexico City race had its `start_date` one day ahead while it had
three frames. So record evidence is checked first, the clock only speaks when there are no records
at all (`weekend.session_state`).

### Why the card label is short

The card is ~168px. The long label ("Antrenman 1" [Practice 1]) wrapped onto two lines on a real phone
and the badge got squeezed next to it; the absolutely-positioned badge + fixed `padding-right` solution
broke a second time ("ANTRENM…KAYIT YOK" [PRACT…NO RECORD] — the badge landed on top of the label). The right
solution is not a pixel budget but a **content-sized column**: the card header is
`grid-template-columns: minmax(0,1fr) auto`, the label wraps, it is never clipped.
The labels are `store.KIND_SHORT` (`Ant. 1`), the long counterpart stays in the briefing as
`store.KIND_LABELS`; the two sit side by side so they do not drift.

### The card grid and the briefing use the same calculation

`weekend.py` produces two outputs but from **a single calculation**: `overview()` the card grid,
`summary_block()` the briefing matrix. If the two were written separately they would produce two
different numbers for the same weekend — the error this project has fallen into again and again. The summary is memoized
by weekend key + (frame, lap, state) fingerprint; it is refreshed as the live session
grows.

The summary holds a driver × session matrix; cell content changes with session type
(practice: fastest lap / clean long-stint median (number of laps); qualifying: fastest
lap + grid; race/sprint: finishing position / fastest lap). Clean lap = laps within a 7% band
of the driver's best lap; if there are fewer than 3 clean laps the median is
**not given** — deriving long-stint pace from a single lap would be fabrication.

### How a sprint weekend is known

The Ergast-compatible calendar has no sprint flag. Three tiers:

1. If the weekend has an `SQ`/`SP` record → sprint (evidence).
2. If that season/round is in the `sprint_rounds` table → sprint (curated, entered by hand).
3. If none of these → **assumption of a normal weekend**, and the surface says so
   as `shape_source=default`; it does not print an invented calendar.

Marked with `store.put_sprint_round(season, round)`.


## Session state: when "finished" is correct

Two separate questions were being answered with a single word and both produced wrong answers:

| Actual state | What it used to say | Now |
|---|---|---|
| The Mexican race had not started yet (`start_date` in the future, 3 stray frames exist) | **finished** | `başlamadı` [not started] |
| Australian GP finished, the record is cut off at 57/58 | **not finished, checkered flag not visible** | `bitti · kayıt 57/58'te kesiliyor` [finished · record cuts off at 57/58] |

`weekend.race_phase()` makes this distinction and returns two flags: **`event_over`**
("should it be told in the past tense?") and **`record_complete`** ("does this record
support a result?"). The card, the archive badge and the briefing line all read the SAME function.

Evidence order (the clock last, because `SessionInfo.StartDate` is the track's local time,
not UTC — Mexico FP1 says 12:30, OpenF1 says 18:30 UTC):

1. If `start_date` is unambiguously in the future → **not started**. The 12-hour margin
   (`_TZ_MARGIN_HRS`) covers all real timezone differences; a stray
   frame cannot pull this into the past.
2. If there are no records at all → **no data**.
3. If the end time of a session filled in from the archive is in the past → **finished**. The mirror does not
   update a finished session.
4. If `final_status` is finished **or** the race distance is complete → **finished, complete**.
5. If the record was updated within the last 90 s → **in progress**. But only if the writing process
   is `source=live`: because `upsert_race` stamps `last_seen` with the moment of writing,
   freshness alone showed four filled-in practice sessions as "currently live".
6. Stale record + definitely past start → **finished**, truncated if the record is truncated.

It is written into the briefing too (`SEANS DURUMU: ... | KAYIT ...` [SESSION STATE: ... | RECORD ...]), because the model, seeing only
`status=Started` + `tur=57/58`, said "not finished yet".

## Missing sessions of past weekends

The live writer only opens a row for the session running at that moment; practice/qualifying
sessions that passed while capture was off had no row in the archive at all (the "why is there no practice
data" question). `tools/backfill_openf1.py --weekend <meeting_key>` (or
`--weekends-season 2026`) adds and fills the missing sessions from OpenF1; it **does not touch an existing
row** (live records are richer). 68 sessions were added for the 2026 season.

Two traps were measured and closed:

- The frame producer had `session: "Race"` **hardcoded**, so a filled-in FP1 told the briefing
  "seans_tipi=Yarış (race) | tur=26/26" [session_type=Race (race) | lap=26/26]. Now `session_ctx` is carried,
  and additionally `_decorate()` forces the identity **from the `races` row** — fixing the writer
  only helps new frames, the 68 sessions had already been written.
- `source=openf1` + a fresh `last_seen` was taken for "live" (item 5 above).

## Scope: which sessions enter the context

The chat is bound to a **session**, but "three practices are done, who's the favourite" is a question about the weekend. Two extremes were rejected: putting the full briefing of every session into the context (~6 × 4.4k tokens, and on top of that the session-type rules collide) and the LLM tool loop (20-30 s per question, ~13k tokens, risk of making up arguments).

`scope.py` resolves the question **before** the LLM — `routing.py`'s pattern:

| Priority | Source | Note |
|---|---|---|
| 1 | UI chips (`scope: [...]`) | human decision; no guessing |
| 2 | `@shortcuts` | explicit intent |
| 3 | owning session | today's behaviour, exactly as-is |

**There is no fourth step** (inferring intent from the sentence): since the welcome grid can send the scope explicitly, it is unnecessary risk.

| Shortcut | Session |
|---|---|
| `@fp1` `@fp2` `@fp3` | Practice 1/2/3 |
| `@q` | Qualifying |
| `@sq` | Sprint qualifying |
| `@sp` | Sprint |
| `@race` | Race |
| `@all` / `@w` | Weekend summary |

`@p1` is **deliberately absent**: in this panel P1 means "first place" everywhere. An unrecognised `@xyz` is not silently swallowed; it is reported in the response's meta. The token is not written into the transcript (the cleaned question is stored), otherwise the next turn's history reads like a scope request and the same blocks get pulled twice.

If the scope does not include the owning session, **the base snapshot changes too**: it was measured, asking `@fp1 @fp2` in a race chat was returning a race-pace answer because the race frame was first in the prompt. Now the first available session in scope becomes the base.

It is shown above the answer: `Veri: Antrenman 1, Antrenman 2` [Data: Practice 1, Practice 2]. Wrong scope silently produces a wrong answer; if the line is visible, it is noticed immediately.

## AI provider selection

**No provider is built in.** Whoever clones the repo enters their own base URL, model and key; if they don't, the assistant says "sağlayıcı tanımsız" [provider undefined] and sends no request outside at all. The **⚙ AI** button in the chat header (`ai_config.py`, `static/shell.js`).

- Profile = (label, base_url, model, api_key); OpenAI-compatible `/chat/completions`. OpenAI, OpenRouter, Groq, Together, local Ollama/llama.cpp — there is a single code path because they all speak the same interface.
- Priority: **selected profile → `F1_AI_KEY` / `F1_AI_BASE` / `F1_AI_MODEL` environment variable → none**.
- **The key never goes back to the browser.** `_row_public()` returns only `has_key` and a **SHA-256 fingerprint** (`sha:29b1d4`). The first version handed out the last 4 characters of the key as a "hint" — that is a real part of the secret, it lands in screenshots and logs; the moment it leaks the key counts as burnt.
- An empty key field **does not delete what is stored** (so that credentials are not lost when the form submits an empty password) and the DB file is chmodded to 0600 after writing.
- A provider may echo the key back in the error body ("invalid api key: sk-…"); that text does not reach the screen or the log without being masked by `scrub()`.
- "Bağlantıyı test et" [Test connection] makes a single-token call before saving: so that a typo doesn't wait 6-110 s on the first real question and come back as an incomprehensible provider error.
- Because the two chat surfaces are separate processes, `/api/ai` is mounted in both; the setting lives in the shared database.

### Why base_url is validated (SSRF)

`base_url` is user input and **the server makes a request to that URL** — the classic SSRF shape. `check_base_url()` runs on every save and every test call:

| target | result |
|---|---|
| `http://127.0.0.1:11434/v1`, `http://localhost:8080/v1` | ✅ local model, the expected setup |
| `https://api.openai.com/v1`, any public https | ✅ |
| `http://169.254.169.254/...`, `metadata.google.internal` | ❌ cloud metadata |
| `http://10.x`, `http://192.168.x`, `http://172.16-31.x` | ❌ (opened by the flag below) |
| `file://`, `ftp://`, `gopher://` | ❌ scheme rejected |
| link-local, multicast, reserved, unspecified IP | ❌ |

The check is two-layered: the literal IP in the URL **and** the addresses the hostname resolves to — `evil.example` can point anywhere. If you run it on your own server on a private network you can open RFC1918 with `F1_AI_ALLOW_PRIVATE=1`; loopback is always open.


## Why the chat is streamed (SSE)

The complaint: *"İstek hatası: HTTP 524 — sunucu JSON yerine HTML döndürdü"* ["Request failed: HTTP 524 — the server returned HTML instead of JSON"]. **524 is not a standard HTTP code**; a CDN or proxy produces it and it has exactly one meaning: *"I didn't get an answer from the server behind in time, so I cut the connection."*

The root cause was architectural. A normal `POST /api/chat` call **did not send a single byte for 30-110 seconds** while the model was thinking. The connection was alive, the request was gone, but silent. An intermediary that gives up on a byte-quiet connection at around 100 seconds answers with its own HTML error page; the panel sees that as "HTML instead of JSON". The answer had been generated (and paid for) yet never reached the user.

At thinking level "high" this silence easily exceeded the limit. The escape hatch (turn thinking off and ask again) meant a second call, so it stretched the duration further and increased the odds of a 524.

### The fix: remove the cause, not the symptom

`POST /api/chat/stream` (separately on live 8099 and archive 8100, because they are separate processes; the line is `chat_request.handle_stream` in one place):

| Event | Content |
|---|---|
| `scope` | which sessions the answer is based on |
| `start` | thinking level + model |
| `thinking` | thinking is in progress (counter only; no text shown) |
| `delta` | a piece of the answer — appended as it arrives |
| `done` | final answer + cost/quality meta |
| `error` | the message to show instead of an answer |

Three separate things keep the connection alive:

1. **Tokens.** Bytes flow as the answer is written; this is the real fix.
2. **Heartbeat.** Thinking tokens are not sent as *content*, so on a heavy thinking question the connection would stay silent anyway. With `CHAT_PING_SECS = 5` an SSE comment line (`: ping`) goes out — EventSource and our own parser ignore it, so it cannot corrupt the answer. 5 s is one twentieth of the ~100 s an intermediary typically waits.
3. **Thinking events** are thinned to one per 200 characters: the panel's "thinking…" counter visibly advances, but hundreds of SSE events are not produced per answer.

### Why not `EventSource`

`EventSource` can only do GET and cannot send a body; we need to carry the question, the scope and the turn. Hence `fetch()` + a hand-written reader (`static/shell.js`, `chatStream`). The parser is deliberately small: our server produces exactly `data: <json>` and `: ping`.

### Three behaviours the streaming path locks in

- **Request body from one place.** `analysis._request_body` produces both the streamed and non-streamed path; model/budget/thinking settings cannot diverge between the two.
- **Meta from one place.** `analysis._answer_meta`; the cost line cannot differ between the two paths.
- **An error is not written into the transcript as an answer.** A failed call does not produce `done`; if "(boş cevap)" ["(empty answer)"] were stored, later turns would think the assistant had answered.

Measured result: the first word of the answer is on screen in **0.3 s** (previously 30-110 s of silence). `tests/test_stream.py` checks the request body, meta parity, the double-append regression, the browser parser (JSON split in two, ping, error event) and the heartbeat interval.

## Sector colours

The tables have S1/S2/S3, IDEAL and LOSS columns; sectors are painted with the F1 colour convention: **purple** = fastest on track in the session, **green** = the driver's own best, **yellow** = slower than their own best.

The decision is made by the **server** (`analysis._sector_status`), the browser only applies the class. A second comparison would start contradicting the IDEAL/LOSS arithmetic right next to it. Colour alone is not enough (colour blindness, sunlight): a line carrying the same colour is placed on the cell's left edge, and the value always stays written.

## Which agent is where

The second line of every briefing says `ORTAM:` — live capture, archive, or a past lap of a live race. Without this line the model confuses a live race with a recording and answers the "last race" question from the wrong race.

The model cannot call tools (one request/one answer). So the "look at the archive" intent is not left to the model: `routing.py` resolves the question **before** the LLM call.

| Question | What happens |
|---|---|
| "what happened on lap 20" | the real rows and sectors of that lap are added to the briefing |
| "what happened in the last race" | the final-lap summary of that race from the archive is added |
| the named race is not in the archive | the archive list is added, the model asks **which race** |

A tool loop was not built: it was measured, one LLM call is 6-10 s and the briefing is ~4.4k tokens; a three-step loop comes to 20-30 s and ~13k tokens per question, along with the risk of fabricating tool arguments in a model with reasoning disabled. The routing is deterministic and testable.

## Tests

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

`tests/test_points.py` exercises the pure arithmetic (table values, DNF, sprint table,
projection, tied points, a driver who is not in the base table, a full 22-driver race).

`tests/ui_test.js` takes the **real source** of the pages and runs it on a small DOM mock
(39 checks): mobile panel open/close and grip preview, keyboard-height compensation,
`fetchJSON` not leaking the HTML body, nav links being correct in both access modes,
table header/cell order, markdown formatting order (XSS), panel CSS precedence.

`tests/layout_test.js` **measures in a real browser** (15 checks): is the panel locked to
46px, is the grip being overridden by flex, is the open panel at 62%, is it pushed up
when the keyboard opens, does `[hidden]` actually hide. Bugs such as CSS precedence, flex
override and percentage math are invisible in a DOM mock — three bugs escaped exactly
this way in that round.

Browser: the `chrome-headless-shell` binary from Chrome for Testing, because on this
machine `/snap/bin/chromium` and `firefox` do not run because of snap-confine.
Install once:

```bash
mkdir -p ~/.local/share/f1-agentic-analyst/tools && cd ~/.local/share/f1-agentic-analyst/tools
curl -sLO https://storage.googleapis.com/chrome-for-testing-public/131.0.6778.85/linux64/chrome-headless-shell-linux64.zip
unzip chrome-headless-shell-linux64.zip
```

If the binary is missing or the service is not up, the test skips itself (it does not
go red).

`tests/smoke_real_db.py` copies the real `data/f1dash.db` file to **a temporary copy**
and does everything on the copy; it does not write to the live archive. What it tests is
not "the unit tests pass" but "it works on the archive we have": schema migration, cache
round-trip, the projection and an **independent recomputation** of the most recent real
race in order (on every row `total = base + race points`, the points of every scored
driver is the table value for their finishing position), double-counting protection.

### Checking the surfaces by hand

Nothing here needs the real database or a race in progress:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8099/api/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/api/races    # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8101/api/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8103/             # 200
timeout 3 curl -sN http://127.0.0.1:8099/api/stream | head -c 120            # data: ...
```

The nav logic must produce the right address when the page is opened on
`127.0.0.1` and when it is opened on a LAN address: the host always comes from
`location`, never from a constant. `tests/ui_test.js` verifies this by running
`shell.js` with a fake `location`/`document`.

## Design source

The first version of the interface was derived from a design prototype: desktop
standings, live + provisional points panel, and mobile standings screens. The prototype
**images are not kept in the repository** — they contained real race data (lap times,
FIA messages, points table) and that data cannot be redistributed under this
repository's licence. All that remains are the CSS tokens the pages use
(`static/theme.css`).

## Chat persistence

The chat lives in the `chat` table, keyed by `race_id`. Refreshing the page, clearing
browser data, even opening it from another browser does not delete the chat. The chat of
the same race is the same on both surfaces — it makes no difference which interface you
open it from.

## Learned preferences (the assistant learns by itself)

Time format, presentation format, language and thinking level are kept as
**preferences** in the `prefs` table. Because these belong to the person and not to the
race, they are not embedded in the race snapshot and do not sit in the browser's
`localStorage` either: preferences should not be lost when browser data is cleared, and
the two surfaces should answer in the same format.

The assistant learns these from the chat — no model call, a deterministic regex table
(`analysis._LEARN_RULES`), that is, the assistant cannot invent a preference on its own:

| What you write | What is learned |
|---|---|
| "süreyi dakika saniye olarak istiyorum" [I want the time in minutes and seconds] | `time_format=mmss` → `1:38.993` |
| "bundan sonra saniye olarak ver" [give it in seconds from now on] | `time_format=sec` → `98.993` |
| "liste halinde" / "madde madde" [as a list / item by item] | `answer_style=list` |
| "tablo" / "düz cümle" [table / plain sentence] | `answer_style=table` / `prose` |
| "ingilizce" / "türkçe" [English / Turkish] | `language=en` / `tr` |
| changing the thinking menu | `reasoning=off…high` |

Learning works **sentence by sentence**: in the message "tek pit stratejileri nasıl
çalıştı, liste halinde dakika saniye olarak" [how did the single-stop strategies work,
as a list in minutes and seconds], the first sentence is a data question (it teaches
nothing), the second is a format instruction. The learned preference is applied **to the
same answer**, not to the next one. The green `tercih:` badge in the chat window shows
what was learned; clicking it resets it (`POST /api/prefs {"reset":true}`). The
preference enters the briefing as a `TERCİH:` line and times are given to the LLM
**already in the requested unit** — the model is not trusted to convert formats.

## Lap data: it's all there, "last 8 laps" is just a window

The `lap_times` table keeps **every lap of every driver** (US GP 2026: 22 drivers, 1163
lap records; the gaps are only those who retired and the final lap — this is written into
the briefing as "kayıt yok: tur …" ["no record: lap …"]). Despite that, the old briefing
only sent `LAST` / `BEST` / `MED_last8` / `TREND`, which is why the assistant said "I
don't have all the laps" — the data was there, it just wasn't in the context.

The briefing now carries the `TÜM TURLAR` block: lap-by-lap times per driver with stint
boundaries (`[HARD 40-56]`) (~16 KB briefing, ~4k tokens). `MED_last8` = the median of
the last 8 laps and `TREND` = the newest minus the oldest of that window; both are
**shortcuts**, not a limit on the data. If the context budget is exceeded, the block
drops to the last 15 laps per driver and says so in the heading.

On top of that, drivers who retired are now identified from their `lap_times` + their
newest frames even though they are no longer in the last frame, and are added to the
table (`store.race_driver_meta`), so they no longer drop out of the briefing entirely.

On laps that have sector data, the briefing line also carries the `S1 S2 S3 IDEAL LOSS`
columns (`analysis._attach_sectors`): IDEAL = the sum of the driver's own best sectors,
LOSS = actual lap − IDEAL. If a sector was not published, `-` is written, not invented;
so the question "where is he losing time" rests on split times, not on a guess.

## Cost visibility: tokens, context, truncation, credit

The meta line under every answer and the thin bar under every surface show the real cost
of the call. None of the numbers is an estimate: all of them come from the provider's
`usage` block.

```
düşünme: off · context 12.8k · cache %5 · cevap 1400/1400 · geçmiş 6/24 · KESİLDİ
```

| Field | Meaning |
|---|---|
| `context` | `prompt_tokens` — the sum of briefing + chat history + the question |
| `cache %` | `cached_tokens / prompt_tokens`; comes out high because the briefing is rebuilt on every turn (which lowers the cost) |
| `cevap n/max` [answer n/max] | `completion_tokens` / budget — `MAX_TOKENS_THINKING` (8000) when thinking is on, `MAX_TOKENS` (1400) when it is off |
| `geçmiş s/t` [history s/t] | how much of the history was sent — the compact policy: the last `HIST_TURNS=6` messages, each `HIST_CHARS=2000` characters |
| `KESİLDİ` [TRUNCATED] | `finish_reason == "length"` → the answer was cut at the limit |
| `düşünme: off (bütçe yetmedi)` [thinking: off (budget not enough)] | the thinking budget was not enough to write the answer, so it was repeated in fast mode |

Truncation used to be **invisible**: the answer ended mid-sentence and that read like a
model error. Now it shows in three places — `KESİLDİ` in the meta line, a yellow warning
under the answer, and the `truncated` counter in `/api/usage`.

### Thinking tokens eat the answer budget — and the ceiling was the cause of the failure (measured)

The provider bills thinking tokens **inside** `completion_tokens`; it does not add them
on top. So a single `max_tokens` covers both the thinking and the answer. 1400 on its own
was not enough for this: the model finished all 1400 on thinking and returned an **empty
answer**.

The first solution was to grow the ceiling (1400 → 3000). That was wrong. When I raised
the ceiling well up (`max_tokens=16000`) and measured, I saw that the model stops on its
own:

| Question | ct | rtok | finish | duration |
|---|---|---|---|---|
| "ikinci stint neden kötüydü" [why was the second stint bad] | 1350 | 920 | `stop` | 39.9 s |
| same question (repeat) | 590 | 482 | `stop` | 31.6 s |
| "tek tek pit yapsa kazanabilir miydi" [could he have won by pitting one by one] | 4962 | 3774 | `stop` | 132.0 s |
| "LEC'in stint 1'i neden yavaştı" [why was LEC's stint 1 slow] | 1350 | 546 | `stop` | 34.0 s |

All four stopped naturally. The natural need varies between 590–4962 tokens; a ceiling
tuned to the average **cuts the tail**. So the 3000 ceiling was itself the cause of the
empty-answer failure it was trying to prevent: "tek tek pit yapsa kazanabilir miydi"
[could he have won by pitting one by one] needs 4962 tokens, and at 3000 all of it went
into thinking with no room left for the answer.

The ceiling is now **8000** (`MAX_TOKENS_THINKING`) and is only **runaway-answer
protection** — clearly above the observed maximum (4962). What limits a call is not the
ceiling but time (`THINK_TIMEOUT=300`). After the measurement, two hard questions were
answered without truncation and without the rescue kicking in (1684 and 2521 tokens).

The cost: "tek tek pit yapsa kazanabilir miydi" [could he have won by pitting one by one]
can take 67–132 s. If speed matters, "düşünme: kapalı" [thinking: off] answers in 2–3 s.

Two safety nets remain:

1. **Empty-answer rescue.** If `finish_reason=length` + empty content arrive, the request
   is repeated once with `reasoning_effort=none`. Once the ceiling went up to 8000 it
   never kicked in on the measured calls (rescue rate 15% → 0%); it stays for a runaway
   answer. The meta says `düşünme: off (bütçe yetmedi)` [thinking: off (budget not
   enough)], the tokens of the two calls are summed (`_merge_usage`), so the cost does
   not look lower than it is.
2. **The "continue" prompt is honest.** `cutNote()` offers a continuation only when there
   is genuinely truncated text; on an empty answer there is no half-finished sentence,
   so it suggests narrowing the question.

`GET /api/usage` (the same on both surfaces, a single `usage` table) gives a summary of
the last 20 answers: `calls`, `avg_prompt`, `prompt_tokens`, `cache_pct`,
`completion_tokens`, `truncated`, `credits_cr` (the remaining credit reported by the
provider) and the raw `last` row. The credit figure is visible in the panel, so running
out of quota does not come as a surprise.

Rows are kept in the `usage` table (`store.usage_add` / `store.usage_summary`), because
"how much did this race cost me" is a summation question and it must still have an answer
after the service restarts.

## License, sources and legal notes

**Code: MIT** — see [LICENSE](LICENSE). It permits everything, including commercial use;
the only condition is to keep the copyright line. The runtime dependencies are also
permissively licensed (FastAPI: MIT, uvicorn/websockets/Starlette: BSD-3-Clause), so they
are compatible with MIT.

### Data sources and their licenses

This project **carries no F1 data in its own repository**: no sample database, no JSON
dump, no static statistics page. `data/` is in .gitignore; whoever clones it collects the
data on their own machine the moment they run it. Licence questions therefore depend on
the licence of the **data source**, not of the **code**, and the terms of the sources are
as follows:

| Source | Use | License / condition | What it does |
|---|---|---|---|
| F1 official live timing (`livetiming.formula1.com`) | WebSocket subscription | Subject to Formula 1's terms of use; it is not an official/commercial API | Live capture (`server.py`) |
| [Jolpica F1](https://github.com/jolpica/jolpica-f1) (`api.jolpi.ca`) | Standings/points data | **Apache-2.0** (successor to the Ergast API) | Standings |
| [OpenF1](https://openf1.org/) (`api.openf1.org`) | Historical session completion | **CC BY-NC-SA 4.0** — **non-commercial** only, share-alike under the same license | `tools/backfill_openf1.py`, `tools/audit_coverage.py` |

**The most important point:** OpenF1 data is closed to commercial use. Even though this
repository is MIT as code, **the use of data pulled from OpenF1 in a commercial product
cannot be granted by this repository** — that data's licence is CC BY-NC-SA 4.0 and the
NC restriction comes from the source. Use for personal/learning purposes only in your own
installation falls outside of this. If you are going to build a commercial product, you
must separately negotiate a licence with OpenF1 (there is a "contact" link at
openf1.org).

The F1 live timing WebSocket is not an official API either; Formula 1's terms of use and
copyrights apply. If you will use it beyond personal use, you need a licence agreement
with F1.

### Trademark

The project name and the team/driver names in the interface are used for **identifying**
purposes only. "F1", "FORMULA ONE", "FORMULA 1", "FIA FORMULA ONE WORLD CHAMPIONSHIP",
"GRAND PRIX" and the related marks are trademarks of **Formula One Licensing B.V.**. This
project is **not associated with, endorsed by or sponsored by Formula 1, the FIA or
Formula One Management**. The tyre compound names (SOFT/MEDIUM/HARD) and their colours
also refer to F1/Pirelli marks; they are used only in data visualisation.

There is **no** F1 logo, broadcast footage, team logo or copyrighted photograph in the
repository. The design prototype's screenshots were removed from the repository for this
reason too: they contained real race data and FIA race-control text. The only remaining
image is `design/pwa-icon.svg` — an app icon drawn by the project itself, carrying no
third-party content; `tools/make_icons.py` generates PNGs from it.

### If you are going to build a commercial product — checklist

1. Remove OpenF1 data from the product or negotiate a commercial licence with OpenF1.
2. Do not use the F1 live feed commercially without a licence agreement with F1.
3. Do not use the "F1" designation as a product name/brand; even in identifying use, do
   not position it in a way that creates confusion (e.g. logo, official look).
4. Team/driver names may be used as factual data; logos may not.
