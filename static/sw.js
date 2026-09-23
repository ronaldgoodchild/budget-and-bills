// Minimal service worker: enables "Add to Home Screen" and caches the app
// shell so the page frame loads even with a flaky connection. API requests
// always go to the network - this app is useless offline anyway since it
// needs your local data.
const CACHE_NAME = "budget-app-shell-v1";
const SHELL_ASSETS = ["/", "/static/style.css", "/static/app.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.url.includes("/api/")) return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
