"""Vercel Python entry: `vercel deploy` from the repo root serves hosted_facilitator on /*.

Set NANO_RPC_URL in the project's environment (optional; a public node is the default).
No other setting exists, and no secret belongs here.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hosted_facilitator as hf  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _serve(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(min(n, hf.MAX_BODY + 1)) if n else b""
        import json
        status, out = hf.handle(self.command, self.path.split("?", 1)[0], body, hf.default_node(), hf._GUARD,
                                self.headers.get("x-payment", ""), self.headers.get("host", ""),
                                self.path.split("?", 1)[1] if "?" in self.path else "")
        blob = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    do_GET = do_POST = _serve
