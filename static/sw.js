/* F1 Agentic Analyst — service worker.
 *
 * Served by pwa.py at `/sw.js` on every surface (root, not /static: a worker's
 * scope is its own directory, so `/static/sw.js` could never see a page
 * navigation). `%CACHE_VERSION%` and `%PRECACHE%` are substituted by the
 * server from the same list the Python side uses, so the two cannot disagree
 * about what is supposed to be offline-ready.
 *
 * The strategy is chosen per request, not globally, because the three kinds of
 * thing this app fetches fail in different ways:
 *
 *   navigation        network first, cache as the offline fallback. The shell
 *                     HTML is what an installed app opens with; showing the
 *                     cached copy instantly would hide a deploy, so the network
 *                     is asked first and the cache only covers "no network".
 *   GET /api/…        network first, cache as fallback. A stale snapshot is
 *                     honest -- every surface prints the snapshot's own
 *                     timestamp and shell.js flags the offline state -- where
 *                     an empty page would just look broken.
 *   other GET (css/js/
 *   icons/manifest)   stale-while-revalidate: answer from cache immediately,
 *                     refresh in the background. These are the assets that
 *                     make the install feel instant.
 *   POST/DELETE       network only. Chat, prefs and refresh are writes: a
 *                     "success" the server never saw is worse than an error.
 *   /api/stream       bypassed (SSE). It never ends, so caching it would hang
 *                     a Cache API write forever.
 */
"use strict";

const CACHE = "%CACHE_VERSION%";
const PRECACHE = %PRECACHE%;

/* Precache is all-or-nothing on purpose. `cache.addAll` rejects if ONE file
   404s, and a half-populated cache is the worst state: it exists, so the
   offline fallback looks available, and it fails on the asset the page
   actually needs. A worker that refuses to install is visible in DevTools;
   this is not. */
self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(PRECACHE);
    // No skipWaiting(): a new worker must not take over a page that is
    // mid-race. It activates when the last old client goes away.
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.map((n) => (n === CACHE ? null : caches.delete(n))));
    await self.clients.claim();
  })());
});

function isStream(url) {
  return url.pathname.endsWith("/api/stream");
}

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(request);
    // Only store a complete, successful, same-origin response. An opaque or
    // error response cached here would be served as the offline page forever.
    if (res && res.ok && res.type === "basic") {
      cache.put(request, res.clone());
    }
    return res;
  } catch (err) {
    const hit = await cache.match(request);
    if (hit) return hit;
    if (fallbackUrl) {
      const shell = await cache.match(fallbackUrl);
      if (shell) return shell;
    }
    throw err;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  const net = fetch(request).then((res) => {
    if (res && res.ok && res.type === "basic") {
      cache.put(request, res.clone());
    }
    return res;
  }).catch(() => null);
  if (hit) {
    // Do not await the refresh: the cached copy is the answer, the fetch is
    // housekeeping. Errors are already swallowed above.
    return hit;
  }
  const res = await net;
  if (res) return res;
  return new Response("çevrimdışı ve önbellekte yok", {
    status: 504, headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;

  if (req.method !== "GET") return;            // writes: straight to network
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // cross-surface nav links
  if (isStream(url)) return;                   // SSE

  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, "./"));
    return;
  }
  if (url.pathname.includes("/api/")) {
    event.respondWith(networkFirst(req, null));
    return;
  }
  event.respondWith(staleWhileRevalidate(req));
});
