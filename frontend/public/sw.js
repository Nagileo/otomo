const CACHE = "otomo-shell-v2";
const SHELL_PAGES = ["/", "/chat", "/discover", "/library", "/workspace", "/settings/subscriptions"];
const SHELL = [...SHELL_PAGES, "/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});

function privateOrApi(request, url) {
  return request.method !== "GET"
    || request.headers.has("authorization")
    || url.origin !== self.location.origin
    || url.pathname.startsWith("/api/")
    || url.pathname.startsWith("/auth/");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (privateOrApi(request, url)) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).then((response) => {
      // Only cache static product shells. Dynamic subject/share pages may contain
      // personalized or private-preview data and must never enter Cache Storage.
      if (response.ok && SHELL_PAGES.includes(url.pathname) && !url.search) {
        caches.open(CACHE).then((cache) => cache.put(url.pathname, response.clone()));
      }
      return response;
    }).catch(async () => {
      if (SHELL_PAGES.includes(url.pathname) && !url.search) return (await caches.match(url.pathname)) || (await caches.match("/"));
      return caches.match("/");
    }));
    return;
  }
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icon") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(caches.match(request).then((cached) => {
      const network = fetch(request).then((response) => {
        if (response.ok) caches.open(CACHE).then((cache) => cache.put(request, response.clone()));
        return response;
      }).catch(() => cached);
      return cached || network;
    }));
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { body: event.data ? event.data.text() : "Otomo 有一条新的订阅更新" };
  }
  const title = payload.title || "Otomo 更新";
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, {
      body: payload.body || "打开 Otomo 查看详情",
      icon: payload.icon || "/icon-192.png",
      badge: payload.badge || "/icon-192.png",
      tag: payload.tag || "otomo-update",
      data: { url: payload.url || "/settings/subscriptions" },
    }),
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      clients.forEach((client) => client.postMessage({ type: "otomo-push-received" }));
    }),
  ]));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
    for (const client of clients) {
      if (client.url.startsWith(self.location.origin) && "focus" in client) {
        if ("navigate" in client) {
          return client.navigate(target).then((navigated) => navigated ? navigated.focus() : client.focus());
        }
        return client.focus();
      }
    }
    return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
  }));
});
