"""Serve answer.json for any GET path."""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BODY = Path(__file__).with_name("answer.json").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(BODY)

    do_HEAD = do_GET


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
