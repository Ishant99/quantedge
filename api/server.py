import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# API key auth: set API_SECRET_KEY env var or user_settings.json to enable.
# If blank, auth is disabled (localhost dev convenience).
def _get_api_key() -> str:
    try:
        import settings.manager as _S
        return os.environ.get("API_SECRET_KEY") or _S.get("API_SECRET_KEY", "")
    except Exception:
        return os.environ.get("API_SECRET_KEY", "")

from services.api_data import (  # noqa: E402
    activity_payload,
    analytics_summary_payload,
    api_meta,
    health_payload,
    overview_payload,
    portfolio_payload,
    review_markdown,
    review_payload,
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, body_text: str, content_type: str = "text/plain; charset=utf-8"):
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _is_authorised(self) -> bool:
        """Check Bearer token or X-API-Key header. Skipped if no key configured."""
        required = _get_api_key()
        if not required:
            return True  # auth disabled
        auth_header = self.headers.get("Authorization", "")
        api_key_header = self.headers.get("X-API-Key", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:] == required
        return api_key_header == required

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.end_headers()

    def do_POST(self):
        """
        POST /webhook/signal — accept an inbound trade signal.

        Body (JSON):
          symbol     : str   — e.g. "RELIANCE"
          action     : str   — "BUY" | "SELL" | "HOLD"
          price      : float — optional; fetched live if omitted
          confidence : float — 0.0–1.0 (default 0.65)
          source     : str   — "tradingview" | "manual" | etc.

        Returns:
          {"ok": true, "trade_id": <int>, "symbol": ..., "action": ...}
          or {"ok": false, "error": "..."}
        """
        if not self._is_authorised():
            self._send_json(401, {"ok": False, "error": "Unauthorised"})
            return

        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        if path != "/webhook/signal":
            self._send_json(404, {"ok": False, "error": f"Unknown POST endpoint: {path}"})
            return

        # Read body
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw    = self.rfile.read(length) if length else b"{}"
            body   = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": f"Invalid JSON body: {exc}"})
            return

        symbol     = str(body.get("symbol", "")).upper().strip()
        action     = str(body.get("action", "")).upper().strip()
        price      = body.get("price")
        confidence = float(body.get("confidence", 0.65))
        source     = str(body.get("source", "webhook"))
        reasoning  = body.get("reasoning", f"Webhook signal from {source}")

        # Basic validation
        if not symbol:
            self._send_json(422, {"ok": False, "error": "Missing required field: symbol"})
            return
        if action not in ("BUY", "SELL", "HOLD"):
            self._send_json(422, {"ok": False,
                                  "error": f"action must be BUY | SELL | HOLD, got '{action}'"})
            return
        if not (0.0 <= confidence <= 1.0):
            self._send_json(422, {"ok": False,
                                  "error": "confidence must be 0.0–1.0"})
            return

        if action == "HOLD":
            self._send_json(200, {"ok": True, "trade_id": None,
                                  "symbol": symbol, "action": "HOLD",
                                  "message": "HOLD — no position opened"})
            return

        # Route to paper executor
        try:
            import sys, os
            sys.path.insert(0, ROOT)
            from execution.executor import get_executor
            from strategy.engine import TradeSignal

            if price is not None:
                price = float(price)

            sig = TradeSignal(
                symbol=symbol,
                action=action,
                entry_price=price or 0.0,
                confidence=confidence,
                p_direction=confidence,
                reasoning=f"[{source}] {reasoning}",
                setup_type="webhook",
            )

            executor = get_executor()
            result   = executor.execute(sig)
            trade_id = result.get("trade_id") if isinstance(result, dict) else None
            status   = result.get("status", "unknown") if isinstance(result, dict) else str(result)

            self._send_json(200, {
                "ok": True,
                "trade_id": trade_id,
                "symbol": symbol,
                "action": action,
                "price": price,
                "confidence": confidence,
                "source": source,
                "status": status,
            })

        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def do_GET(self):
        if not self._is_authorised():
            self._send_json(401, {"ok": False, "error": "Unauthorised — provide Bearer token or X-API-Key header"})
            return

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
            "/api/review": lambda: review_payload(),
        }

        if path == "/api/review.md":
            try:
                self._send_text(200, review_markdown(), "text/markdown; charset=utf-8")
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc), "path": path})
            return

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
