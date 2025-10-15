// sw.js
const CACHE_VERSION = "pixi-v3"; // ←更新時に必ず変える
const STATIC_ASSETS = [
  "/",                      // ルート（必要なければ外してOK）
  "/static/style.css",
  "/static/app.js",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_VERSION).then((c) => c.addAll(STATIC_ASSETS))
  );
  self.skipWaiting(); // 新SWを即有効化準備
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter(k => k !== CACHE_VERSION).map(k => caches.delete(k)))
    ).then(() => self.clients.claim()) // 既存タブも即このSWに
  );
});

// HTML: network-first（オフライン時だけキャッシュにフォールバック）
async function handleHTML(req) {
  try {
    const resp = await fetch(req);
    const cache = await caches.open(CACHE_VERSION);
    cache.put(req, resp.clone());
    return resp;
  } catch {
    const cached = await caches.match(req);
    return cached || new Response("Offline", { status: 503 });
  }
}

// CSS/JS: stale-while-revalidate（まずキャッシュ→裏で更新）
async function handleAsset(req) {
  const cached = await caches.match(req);
  const pFetch = fetch(req)
    .then((resp) => caches.open(CACHE_VERSION).then((c) => c.put(req, resp.clone())).then(() => resp))
    .catch(() => null);
  return cached || (await pFetch) || new Response("/* offline */", { status: 503 });
}

// 画像: cache-first
async function handleImage(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  try {
    const resp = await fetch(req);
    const cache = await caches.open(CACHE_VERSION);
    cache.put(req, resp.clone());
    return resp;
  } catch {
    return new Response("", { status: 404 });
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  const isSameOrigin = url.origin === self.location.origin;

  // HTMLナビゲーション
  if (req.mode === "navigate") {
    e.respondWith(handleHTML(req));
    return;
  }

  // 同一オリジンの静的アセットを判定
  if (isSameOrigin && url.pathname.startsWith("/static/")) {
    if (url.pathname.endsWith(".css") || url.pathname.endsWith(".js")) {
      e.respondWith(handleAsset(req));
      return;
    }
    if (/\.(png|jpg|jpeg|gif|webp|svg|ico)$/i.test(url.pathname)) {
      e.respondWith(handleImage(req));
      return;
    }
  }

  // それ以外はデフォルト：まずネット、失敗したらキャッシュ
  e.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});
