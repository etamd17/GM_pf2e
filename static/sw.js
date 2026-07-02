// PF2E GM Dashboard -- Service Worker (PWA offline shell)
// Cache strategy: static assets cache-first (their URLs are ?v= cache-busted
// per deploy), API network-first, HTML navigations NETWORK-ONLY.
//
// v2: HTML pages are never cached or served from cache. v1 cached each page
// under its request URL -- including redirected responses, so /gm (which 302s
// to /cosmere/gm while a Cosmere campaign is active) got the COSMERE page
// cached under the /gm key. Any later fetch hiccup then served the wrong
// game system's UI from cache, and the stale shell's dead JS made the app
// look broken ("campaign won't switch"). Bumping CACHE_NAME evicts every
// browser's poisoned v1 cache on its next service-worker update.

var CACHE_NAME = 'pf2e-gm-v2';
var SHELL_URLS = [
    '/static/css/system.css',
];

// Install: cache the app shell
self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function(cache) {
            return cache.addAll(SHELL_URLS).catch(function() {
                // Non-fatal: some URLs may not exist yet
            });
        })
    );
    self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(names) {
            return Promise.all(
                names.filter(function(n) { return n !== CACHE_NAME; })
                     .map(function(n) { return caches.delete(n); })
            );
        })
    );
    self.clients.claim();
});

// Fetch: network-first for API, cache-first for static assets
self.addEventListener('fetch', function(event) {
    var url = new URL(event.request.url);

    // Skip SSE streams and POST requests
    if (event.request.method !== 'GET') return;
    if (url.pathname === '/api/events') return;

    // API calls: network-first
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request).catch(function() {
                return caches.match(event.request);
            })
        );
        return;
    }

    // Static assets: cache-first, then network
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then(function(cached) {
                if (cached) return cached;
                return fetch(event.request).then(function(response) {
                    if (response.ok) {
                        var clone = response.clone();
                        caches.open(CACHE_NAME).then(function(cache) {
                            cache.put(event.request, clone);
                        });
                    }
                    return response;
                });
            })
        );
        return;
    }

    // HTML pages: NETWORK-ONLY. Never cache.put a page and never serve one
    // from cache -- a cached page is a snapshot of one campaign/system/session
    // state and goes stale (or lands under a redirected URL's key) the moment
    // the GM switches tables. Offline gets the synthesized fallback below.
    event.respondWith(
        fetch(event.request).catch(function() {
            return new Response(
                    '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline</title>'
                    + '<style>body{font-family:system-ui;background:#0c0a07;color:#f0ead7;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}'
                    + '.c{text-align:center;padding:2rem;}'
                    + 'h1{font-size:24px;color:#c9a34e;margin-bottom:8px;}'
                    + 'p{color:#8e8369;font-size:14px;}</style></head>'
                    + '<body><div class="c"><h1>PF2E Dashboard</h1><p>You appear to be offline. Check your connection and try again.</p></div></body></html>',
                {headers: {'Content-Type': 'text/html'}}
            );
        })
    );
});
