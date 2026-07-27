const STATIC_CACHE = "lcdash-static-v6";
const STATIC_ASSETS = [
    "/static/vendor/bootstrap/bootstrap.min.css",
    "/static/vendor/bootstrap-icons/bootstrap-icons.css",
    "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
    "/static/css/lcdash-core.css",
    "/static/css/lcdash-dashboard.css",
    "/static/css/lcdash-mobile.css",
    "/static/js/lcdash-mobile.js",
    "/static/js/lcdash-dashboard.js",
    "/static/js/lcdash-time.js",
    "/static/img/logan911-logo.png",
    "/static/manifest.webmanifest"
];

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(STATIC_CACHE).then(function (cache) {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys
                    .filter(function (key) {
                        return key.startsWith("lcdash-static-") && key !== STATIC_CACHE;
                    })
                    .map(function (key) {
                        return caches.delete(key);
                    })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener("fetch", function (event) {
    const request = event.request;
    const url = new URL(request.url);

    if (
        request.method !== "GET" ||
        url.origin !== self.location.origin ||
        !STATIC_ASSETS.includes(url.pathname)
    ) {
        return;
    }

    event.respondWith(
        fetch(request)
            .then(function (response) {
                if (response.ok) {
                    const copy = response.clone();
                    caches.open(STATIC_CACHE).then(function (cache) {
                        cache.put(request, copy);
                    });
                }
                return response;
            })
            .catch(function () {
                return caches.match(request);
            })
    );
});
