"""Run CUBIC on your own machine with local stand-in data.

    py dev/serve.py            (Windows)
    python3 dev/serve.py       (macOS / Linux)

Then open http://localhost:8800/ . Standard library only.
The page is served from index.html unchanged, with dev/fake-claude.js loaded
first so sign-in, shared data and the timer work without Claude.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT", "8800"))
MARK = "<script>\n'use strict';"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            html = (ROOT / "index.html").read_text(encoding="utf-8")
            if "bare=1" in self.path:
                # As served on Vercel: no stand-in, the page falls back to this browser's own storage.
                body, ctype = html.encode("utf-8"), "text/html; charset=utf-8"
            elif MARK not in html:
                body, ctype = b"index.html no longer contains the script marker dev/serve.py looks for.", "text/plain"
            else:
                body = html.replace(MARK, '<script src="/dev/fake-claude.js"></script>\n' + MARK, 1).encode("utf-8")
                ctype = "text/html; charset=utf-8"
        elif path == "/dev/fake-claude.js":
            body, ctype = (ROOT / "dev" / "fake-claude.js").read_bytes(), "application/javascript"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"CUBIC (local data): http://localhost:{PORT}/")
    print("Second person: add ?as=name   Clear local data: add ?reset=1   As on Vercel: add ?bare=1")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
