#!/usr/bin/env python3
"""
Serve the project root (where index.html and coi-serviceworker.js live) over
https, for local testing.

python3 -m http.server has no TLS option in the standard library, so this wraps
it with a self-signed cert. Browsers will warn about the cert (expected for a
local self-signed one) -- click through "advanced / proceed anyway". Needed
because the real deployment target is any http(s) host; this is just a stand-in
for local testing without actually deploying anywhere.

    python3 web/serve_https.py [port]            # uses web/.certs/{cert,key}.pem
"""

import http.server
import ssl
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # index.html and coi-serviceworker.js live at the project root
CERT = HERE / ".certs" / "cert.pem"
KEY = HERE / ".certs" / "key.pem"


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    if not CERT.exists() or not KEY.exists():
        raise SystemExit(
            f"missing {CERT} / {KEY} -- generate a self-signed cert first:\n"
            f'openssl req -x509 -newkey rsa:2048 -nodes -keyout "{KEY}" -out "{CERT}" '
            f'-days 365 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"'
        )

    import os

    os.chdir(ROOT)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))
    # ThreadingHTTPServer, not plain HTTPServer: wrapping the *listening* socket means each
    # accept() does the TLS handshake inline, which -- single-threaded -- blocks the whole
    # listener from accepting anything else until that handshake finishes. A real browser opens
    # several connections in parallel on page load, so a plain HTTPServer here is flaky: some
    # requests hang (and everything behind them in the queue hangs with it) even though a single
    # sequential request works fine. Threading gives each handshake its own thread.
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), http.server.SimpleHTTPRequestHandler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print(f"Serving {ROOT} on https://localhost:{port}/index.html (self-signed cert)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
