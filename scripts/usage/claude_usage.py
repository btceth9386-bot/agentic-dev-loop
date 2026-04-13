#!/usr/bin/env python3
"""Fetch Claude Code plan / weekly usage limits from the OAuth usage endpoint.

This is a Python port of scripts/usage/claude_usage.sh with:
  - retry-after aware backoff on 429 responses (bounded)
  - best-effort extraction of plan / 5h / weekly limits from the JSON payload

The token is read from the macOS keychain entry that Claude Code creates
("Claude Code-credentials"). On non-macOS, set CLAUDE_CODE_OAUTH_TOKEN in the
environment as a fallback.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"


def read_token() -> Optional[str]:
    """Read the OAuth access token from macOS keychain (falls back to env var)."""
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return env_token

    if sys.platform != "darwin":
        return None

    try:
        out = subprocess.check_output(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8").strip()
    except subprocess.CalledProcessError:
        return None

    if not out:
        return None

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        # Some entries may be a raw token string.
        return out

    return (
        data.get("claudeAiOauth", {}).get("accessToken")
        or data.get("accessToken")
    )


def fetch_usage(token: str, max_retries: int = 2,
                max_retry_wait: float = 30.0) -> Dict[str, Any]:
    """Call the usage endpoint, honoring Retry-After on 429 up to max_retry_wait."""
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
    }

    last_error: Optional[Dict[str, Any]] = None

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(USAGE_URL, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
                return {"ok": True, "status": resp.status, "body": json.loads(body)}
        except urllib.error.HTTPError as e:
            body_raw = e.read().decode("utf-8", errors="ignore")
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                body = {"raw": body_raw}

            last_error = {"ok": False, "status": e.code, "body": body}

            if e.code == 429 and attempt < max_retries:
                retry_after = e.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 2.0
                if wait > max_retry_wait:
                    # Server wants us to wait longer than we're willing -- give up.
                    last_error["retry_after_seconds"] = wait
                    return last_error
                time.sleep(wait)
                continue
            return last_error
        except urllib.error.URLError as e:
            last_error = {"ok": False, "status": None, "body": {"error": str(e)}}
            if attempt < max_retries:
                time.sleep(2.0)
                continue
            return last_error

    return last_error or {"ok": False, "status": None, "body": {"error": "unknown"}}


def _find_percent(node: Any, keys: tuple) -> Optional[float]:
    """Walk the JSON tree for the first numeric value whose key matches any of `keys`."""
    if isinstance(node, dict):
        for k, v in node.items():
            kl = k.lower()
            if any(key in kl for key in keys) and isinstance(v, (int, float)):
                return float(v)
        for v in node.values():
            found = _find_percent(v, keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_percent(v, keys)
            if found is not None:
                return found
    return None


def _find_value(node: Any, keys: tuple) -> Any:
    """Walk the JSON tree for the first value whose key matches any of `keys`."""
    if isinstance(node, dict):
        for k, v in node.items():
            if any(key == k.lower() for key in keys):
                return v
        for v in node.values():
            found = _find_value(v, keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_value(v, keys)
            if found is not None:
                return found
    return None


def parse_usage(body: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort extraction -- the API shape isn't documented, so we probe common keys."""
    return {
        "plan": _find_value(body, ("plan", "subscription", "tier")),
        "five_hour_percent_used": _find_percent(body, ("five_hour", "5h", "session", "shortterm")),
        "weekly_percent_used": _find_percent(body, ("weekly", "week")),
        "resets_at": _find_value(body, ("resets_at", "reset_at", "resetsat", "resetat", "reset")),
    }


def main() -> int:
    token = read_token()
    if not token:
        print(json.dumps({
            "ok": False,
            "error": "no_token",
            "message": "Could not read token from keychain. Set CLAUDE_CODE_OAUTH_TOKEN or log in via Claude Code.",
        }, indent=2))
        return 1

    result = fetch_usage(token)
    parsed = parse_usage(result.get("body") or {}) if result.get("ok") else None

    out = {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "parsed": parsed,
        "body": result.get("body"),
    }
    if "retry_after_seconds" in result:
        out["retry_after_seconds"] = result["retry_after_seconds"]

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
