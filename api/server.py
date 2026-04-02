import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.api_data import (  # noqa: E402
    activity_payload,
    analytics_summary_payload,
    api_meta,
    health_payload,
    overview_payload,
    portfolio_payload,
    signals_payload,
    watchlist_payload,
)


def _int_query(query: dict, name: str, default: int, lower: int, upper: int) -> int:
    raw = query.get(name, [default])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


class QuantEdgeAPIHandler(BaseHTTPRequestHandler):
    server_version = "QuantEdgeAPI/0.1"

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        routes = {
            "/": lambda: api_meta(),
            "/health": lambda: health_payload(),
            "/api/health": lambda: health_payload(),
            "/api/overview": lambda: overview_payload(),
            "/api/portfolio": lambda: portfolio_payload(),
            "/api/signals": lambda: signals_payload(limit=_int_query(query, "limit", 25, 1, 200)),
            "/api/watchlist": lambda: watchlist_payload(limit=_int_query(query, "limit", 8, 1, 50)),
            "/api/activity": lambda: activity_payload(limit=_int_query(query, "limit", 12, 1, 100)),
            "/api/analytics/summary": lambda: analytics_summary_payload(),
        }

        handler = routes.get(path)
        if not handler:
            self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}", "meta": api_meta()})
            return

        try:
            self._send_json(200, handler())
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc), "path": path})

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))


def run():
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), QuantEdgeAPIHandler)
    print(f"QuantEdge API serving on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
