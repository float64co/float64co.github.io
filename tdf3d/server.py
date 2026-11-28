"""Serve the built game: a Flask app running on Tornado's HTTP server."""
import argparse
import asyncio
import mimetypes
from pathlib import Path

from flask import Flask, send_from_directory
from tornado.httpserver import HTTPServer
from tornado.wsgi import WSGIContainer

DIST = Path(__file__).parent / "client" / "dist"
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/wasm", ".wasm")

app = Flask(__name__, static_folder=None)


@app.get("/")
def index():
    return send_from_directory(DIST, "index.html")


@app.get("/<path:name>")
def asset(name):
    return send_from_directory(DIST, name)


async def serve(host, port):
    HTTPServer(WSGIContainer(app)).listen(port, host)
    print(f"http://{host}:{port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not (DIST / "index.html").exists():
        raise SystemExit("Build the client first: cd client && npm run build")
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        pass
