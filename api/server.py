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
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        # Kite callback is a Zerodha server redirect — no auth header is sent.
        # Handle it before the auth gate so the OAuth flow isn't blocked.
        if path == "/webhook/kite_callback":
            self._handle_kite_callback(parsed)
            return

        if not self._is_authorised():
            self._send_json(401, {"ok": False, "error": "Unauthorised"})
            return

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

    # ------------------------------------------------------------------
    # Kite OAuth callback
    # ------------------------------------------------------------------

    def _handle_kite_callback(self, parsed):
        """
        POST /webhook/kite_callback — exchange Zerodha request_token for access_token.

        Accepts request_token via:
          - Query string: /webhook/kite_callback?request_token=XXX
          - JSON body:    {"request_token": "XXX"}

        On success saves token to KITE_ACCESS_TOKEN_FILE and returns:
          {"ok": true, "message": "Kite authenticated", "token_preview": "XXXX…"}
        """
        # Try query string first
        qs = parse_qs(parsed.query)
        request_token = qs.get("request_token", [None])[0]

        # Fallback: JSON body
        if not request_token:
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw    = self.rfile.read(length) if length else b"{}"
                body   = json.loads(raw.decode("utf-8"))
                request_token = body.get("request_token", "")
            except Exception:
                pass

        if not request_token:
            self._send_json(422, {
                "ok": False,
                "error": "Missing required field: request_token "
                         "(pass via query string or JSON body)",
            })
            return

        try:
            from execution.kite_login import exchange_token
            access_token = exchange_token(request_token)

            # If called by a browser (GET redirect), return a friendly HTML page
            accept = self.headers.get("Accept", "")
            if "text/html" in accept:
                html = (
                    "<html><body style='font-family:monospace;background:#0d0d0d;"
                    "color:#00C805;padding:40px;'>"
                    "<h2>✓ Kite Authenticated</h2>"
                    "<p>Access token saved. You can close this tab and return to "
                    "the QuantEdge dashboard.</p>"
                    f"<p style='color:#555;font-size:12px;'>Token: {access_token[:8]}…</p>"
                    "</body></html>"
                )
                self._send_text(200, html, "text/html; charset=utf-8")
            else:
                self._send_json(200, {
                    "ok":            True,
                    "message":       "Kite authenticated — access_token saved",
                    "token_preview": access_token[:8] + "…",
                })
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def do_GET(self):
        parsed = urlparse(self.path)
        query  = parse_qs(parsed.query)
        path   = parsed.path.rstrip("/") or "/"

        # Zerodha OAuth redirect — MUST be before auth gate.
        # The browser redirect from Zerodha carries no Authorization header.
        if path == "/webhook/kite_callback":
            self._handle_kite_callback(parsed)
            return

        if not self._is_authorised():
            self._send_json(401, {"ok": False, "error": "Unauthorised — provide Bearer token or X-API-Key header"})
            return

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
            "/api/kite/status": lambda: self._kite_status_payload(),
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

    # ------------------------------------------------------------------
    # Kite status helper
    # ------------------------------------------------------------------

    def _kite_status_payload(self) -> dict:
        """Return Kite authentication status (used by dashboard)."""
        try:
            from execution.kite_login import is_authenticated, load_access_token
            auth = is_authenticated()
            token = load_access_token()
            from config import KITE_ACCESS_TOKEN_FILE as _KTF
            return {
                "ok":            True,
                "authenticated": auth,
                "token_preview": (token[:8] + "…") if token else "",
                "token_file":    _KTF,
            }
        except Exception as exc:
            return {"ok": False, "authenticated": False, "error": str(exc)}

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))


def run():
    import logging
    _log = logging.getLogger("APIServer")
    if not _get_api_key():
        _log.warning(
            "API_SECRET_KEY is not set — all endpoints are unauthenticated. "
            "Set API_SECRET_KEY in .env or user_settings.json for production use."
        )
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), QuantEdgeAPIHandler)
    print(f"QuantEdge API serving on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
