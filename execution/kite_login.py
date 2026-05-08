# =============================================================================
# execution/kite_login.py — Zerodha Kite Connect OAuth Token Exchange Helper
#
# Kite OAuth flow (two-step):
#   1. Direct the user to Zerodha's login URL (generate_login_url).
#      After login Zerodha redirects to your redirect_url with ?request_token=XXX
#   2. Exchange the request_token + HMAC-SHA256(api_key + request_token + api_secret)
#      for an access_token via POST /session/token (exchange_token).
#      The access_token is valid until ~6:00 AM IST next day.
#
# Storage: access_token is written to KITE_ACCESS_TOKEN_FILE (plain text, one line).
#          The LiveExecutor already reads this file on startup.
#
# Usage (from CLI):
#   python -m execution.kite_login                 # print login URL
#   python -m execution.kite_login <request_token> # exchange and save token
#
# Usage (from code):
#   from execution.kite_login import generate_login_url, exchange_token, is_authenticated
# =============================================================================

import hashlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN_FILE
from utils import get_logger

IST = pytz.timezone("Asia/Kolkata")

logger = get_logger("KiteLogin")

KITE_LOGIN_URL   = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
KITE_SESSION_URL = "https://api.kite.trade/session/token"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def generate_login_url() -> str:
    """
    Return the Zerodha login URL the user must open in a browser.
    After authenticating, Zerodha redirects to your registered redirect_url
    with ?request_token=<token> appended.

    Raises ValueError if KITE_API_KEY is not configured.
    """
    if not KITE_API_KEY:
        raise ValueError(
            "KITE_API_KEY is not set. Configure it in user_settings.json or env."
        )
    url = KITE_LOGIN_URL.format(api_key=KITE_API_KEY)
    logger.info(f"Kite login URL: {url}")
    return url


def exchange_token(request_token: str) -> str:
    """
    Exchange a request_token for an access_token using the Kite REST API.

    Steps:
      1. Compute checksum = SHA256(api_key + request_token + api_secret)
      2. POST to /session/token
      3. Parse access_token from response
      4. Save to KITE_ACCESS_TOKEN_FILE and return

    Args:
        request_token: The token from Zerodha's redirect URL.

    Returns:
        access_token string.

    Raises:
        ValueError: If API credentials not set or exchange fails.
        RuntimeError: On HTTP / JSON error.
    """
    if not KITE_API_KEY or not KITE_API_SECRET:
        raise ValueError(
            "KITE_API_KEY and KITE_API_SECRET must be configured to exchange tokens."
        )
    if not request_token:
        raise ValueError("request_token is empty.")

    # Checksum = SHA256(api_key + request_token + api_secret)
    raw       = KITE_API_KEY + request_token + KITE_API_SECRET
    checksum  = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    try:
        import requests  # type: ignore
    except ImportError:
        raise RuntimeError("'requests' library not installed — pip install requests")

    payload = {
        "api_key":       KITE_API_KEY,
        "request_token": request_token,
        "checksum":      checksum,
    }

    logger.info("Exchanging request_token for access_token …")
    resp = requests.post(KITE_SESSION_URL, data=payload, timeout=15)

    if resp.status_code != 200:
        raise RuntimeError(
            f"Kite token exchange failed — HTTP {resp.status_code}: {resp.text[:400]}"
        )

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Kite returned non-JSON response: {exc}") from exc

    if data.get("status") != "success":
        msg = data.get("message", str(data))
        raise RuntimeError(f"Kite exchange error: {msg}")

    access_token = data["data"]["access_token"]
    _save_token(access_token)
    logger.info("Kite access_token saved to %s", KITE_ACCESS_TOKEN_FILE)
    return access_token


def load_access_token() -> str:
    """
    Read the saved access_token from disk.
    Returns empty string if file missing or empty.
    """
    try:
        token = Path(KITE_ACCESS_TOKEN_FILE).read_text(encoding="utf-8").strip()
        return token
    except Exception:
        return ""


def is_authenticated() -> bool:
    """
    Return True if a non-empty access_token file exists AND it was written today
    (Kite tokens expire at ~6:00 AM IST each day).
    """
    token = load_access_token()
    if not token:
        return False

    try:
        mtime = os.path.getmtime(KITE_ACCESS_TOKEN_FILE)
        # Use IST midnight as boundary — the Oracle VM runs UTC, so datetime.now()
        # would compute the wrong day without an explicit timezone.
        now_ist = datetime.now(IST)
        today_ist_midnight = now_ist.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return mtime >= today_ist_midnight.timestamp()
    except Exception:
        return bool(token)   # file exists but can't stat — assume ok


def revoke_token() -> bool:
    """
    Delete the saved access_token file (forces re-login next time).
    Returns True if file was deleted, False if it didn't exist.
    """
    try:
        Path(KITE_ACCESS_TOKEN_FILE).unlink()
        logger.info("Kite access_token revoked (file deleted).")
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning(f"Could not delete token file: {exc}")
        return False


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _save_token(access_token: str) -> None:
    """Write access_token to the configured file, creating dirs as needed."""
    p = Path(KITE_ACCESS_TOKEN_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(access_token.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kite Connect OAuth helper")
    parser.add_argument(
        "request_token",
        nargs="?",
        default=None,
        help="request_token from Zerodha redirect URL. Omit to print the login URL.",
    )
    args = parser.parse_args()

    if args.request_token:
        try:
            token = exchange_token(args.request_token)
            print(f"\n✓ access_token saved to {KITE_ACCESS_TOKEN_FILE}")
            print(f"  Token (first 12 chars): {token[:12]}…")
        except Exception as err:
            print(f"\n✗ Token exchange failed: {err}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            url = generate_login_url()
            print("\nOpen this URL in your browser to authenticate with Zerodha:\n")
            print(f"  {url}\n")
            print("After login, copy the request_token from the redirect URL and run:")
            print(f"  python -m execution.kite_login <request_token>\n")
        except ValueError as err:
            print(f"\n✗ {err}", file=sys.stderr)
            sys.exit(1)
