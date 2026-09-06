#!/usr/bin/env python3
"""Perform a scan-only or gameplay-safe webMAN refresh through a relay."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def request(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    parser = argparse.ArgumentParser(prog="ps3-refresh")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan-only", action="store_true", help="scan webMAN without reloading XMB")
    group.add_argument("--xmb", action="store_true", help="request a relay-enforced safe scan and XMB reload")
    args = parser.parse_args()
    relay = os.environ.get("PS3_REFRESH_RELAY_URL", "").strip()
    pending = Path(os.environ.get("PS3_REFRESH_PENDING_FILE", "/var/lib/ps3-media-automation/refresh-pending.json"))
    if not relay:
        print("ps3-refresh: PS3_REFRESH_RELAY_URL is not configured", file=sys.stderr)
        return 1
    if args.scan_only:
        url = relay.rstrip("/") + "?mode=scan"
        status, body = request(url)
    else:
        token_file = Path(os.environ.get("PS3_REFRESH_RELAY_TOKEN_FILE", "/etc/ps3-refresh-relay.token"))
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"ps3-refresh: cannot read relay token file: {exc}", file=sys.stderr)
            return 1
        if len(token) < 32:
            print("ps3-refresh: relay token is missing or too short", file=sys.stderr)
            return 1
        status, body = request(relay, {"Content-Length": "0", "X-PS3-Refresh-Token": token})
    try:
        result = json.loads(body or b"{}")
    except json.JSONDecodeError:
        result = {}
    if args.xmb and status == 202 and result.get("action") == "xmb-refresh-pending":
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps({"reason": result.get("reason", "PS3 is not idle")}, indent=2) + "\n", encoding="utf-8")
        print("XMB refresh pending: gameplay was preserved")
        return 3
    if status < 200 or status >= 300:
        print(f"ps3-refresh: relay returned HTTP {status}", file=sys.stderr)
        return 1
    if args.xmb and result.get("action") != "net-refresh-xmb":
        print("ps3-refresh: relay did not confirm a safe XMB refresh", file=sys.stderr)
        return 1
    if args.xmb and pending.exists():
        pending.unlink()
    print("ps3-refresh ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
