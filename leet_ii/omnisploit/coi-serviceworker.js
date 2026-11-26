/*
 * Cross-origin isolation shim.
 *
 * SharedArrayBuffer (needed so web_curses.py's get_wch() can really block via
 * Atomics.wait()) is only available on a page with crossOriginIsolated === true,
 * which in turn needs the Cross-Origin-Opener-Policy and Cross-Origin-Embedder-
 * Policy response headers on the *document* request. Static hosts (GitHub Pages
 * included) don't let you set those. This service worker intercepts the page's
 * own navigation request, re-fetches it, and re-serves the response with those
 * two headers added -- so no server configuration is needed.
 *
 * This is the well-known "coi-serviceworker" technique (originally by Giorgio
 * Vasiliev / gzuidhof), reimplemented here from the same public description.
 */

const COEP_HEADERS = {
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Opener-Policy": "same-origin",
};

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("message", (event) => {
  if (event.data === "coi-reload-if-needed") {
    for (const client of event.source ? [event.source] : []) {
      client.postMessage("coi-ack");
    }
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  // Only navigation (document) and same-origin requests need the headers rewritten;
  // leave the CDN fetches for pyodide's own assets alone (they're cross-origin and
  // already send what they send).
  if (req.cache === "only-if-cached" && req.mode !== "same-origin") return;

  event.respondWith(
    fetch(req)
      .then((response) => {
        if (response.status === 0) return response; // opaque cross-origin response; leave as-is
        const newHeaders = new Headers(response.headers);
        for (const [k, v] of Object.entries(COEP_HEADERS)) newHeaders.set(k, v);
        return new Response(response.body, {
          status: response.status,
          statusText: response.statusText,
          headers: newHeaders,
        });
      })
      .catch((e) => new Response(`coi-serviceworker fetch failed: ${e}`, { status: 500 }))
  );
});
