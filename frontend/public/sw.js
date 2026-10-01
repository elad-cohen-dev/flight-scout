// Flight Scout service worker: keeps the app shell (index.html + its hashed
// assets) so the page renders even while the free-tier server is asleep.
//
// - "/" is network-first: a server that answers within SHELL_TIMEOUT_MS gives
//   the fresh page (and refreshes the cache); a slower one — waking up — gets
//   the cached shell immediately, and the app shows its wake-up banner while
//   the API catches up.
// - /assets/* are content-hashed, so cache-first is always correct. Lazy chunks
//   aren't named in index.html, so instead of pruning by reference the cache
//   keeps the newest MAX_ASSETS files (a deploy adds a handful).
// - Nothing else is touched: /api/* (data, auth) always goes to the network.
//
// To retire it, delete this file: the browser drops a worker whose script 404s.

const CACHE = "flight-scout-shell-v1";
const SHELL_TIMEOUT_MS = 3000;
const MAX_ASSETS = 40;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate" && (url.pathname === "/" || url.pathname === "/index.html")) {
    event.respondWith(shell(event));
  } else if (url.pathname.startsWith("/assets/")) {
    event.respondWith(asset(req));
  }
});

async function shell(event) {
  const network = fetch(event.request).then(async (res) => {
    // Only our own page is a shell — never a host's "waking up" or error page.
    const html = res.ok ? await res.clone().text() : "";
    const ours = html.includes('id="root"');
    if (ours) await (await caches.open(CACHE)).put("/", res.clone());
    return { res, ours };
  });
  event.waitUntil(network.catch(() => {})); // finish refreshing the cache in the background
  const cached = await caches.match("/", { cacheName: CACHE });
  if (!cached) return network.then((r) => r.res);
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), SHELL_TIMEOUT_MS));
  const first = await Promise.race([network.catch(() => null), timeout]);
  return first && first.ours ? first.res : cached;
}

async function asset(req) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res.ok) {
    await cache.put(req, res.clone());
    await prune(cache);
  }
  return res;
}

async function prune(cache) {
  const assets = (await cache.keys()).filter((key) => new URL(key.url).pathname.startsWith("/assets/"));
  // keys() lists entries oldest first
  for (const key of assets.slice(0, Math.max(0, assets.length - MAX_ASSETS))) await cache.delete(key);
}
