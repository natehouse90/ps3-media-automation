#!/usr/bin/env python3
"""Small optional webMAN relay with an explicit idle allow-list and fail-closed reload."""

from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PS3_IP = os.environ.get("PS3_IP", "").strip()
PS3_STATE_URL = os.environ.get("PS3_STATE_URL", "").strip()
PS3_IDLE_MARKER = os.environ.get("PS3_IDLE_MARKER", "XMB_IDLE").strip()
ALLOW_IP = os.environ.get("MANAGEMENT_ALLOW_IP", "").strip()
TOKEN = os.environ.get("REFRESH_RELAY_TOKEN", "")


def fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.status, response.read(4096).decode("utf-8", "replace")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return 0, str(exc)


def is_idle() -> bool:
    if not PS3_STATE_URL or not PS3_IDLE_MARKER:
        return False
    status, body = fetch(PS3_STATE_URL)
    if status != 200 or PS3_IDLE_MARKER not in body:
        return False
    active_markers = ("/dev_bdvd", "PS3_GAME", "GAME_RUNNING", "PLAYING")
    return not any(marker in body for marker in active_markers)


def refresh() -> tuple[int, dict[str, object]]:
    if not PS3_IP or not TOKEN or not ALLOW_IP:
        return 503, {"ok": False, "reason": "relay is not fully configured"}
    if not is_idle():
        return 202, {"ok": True, "action": "xmb-refresh-pending", "reason": "PS3 state was not positively identified as idle"}
    scan_status, _ = fetch(f"http://{PS3_IP}/refresh.ps3")
    if scan_status != 200:
        return 502, {"ok": False, "reason": f"webMAN scan returned HTTP {scan_status}"}
    reload_status, _ = fetch(f"http://{PS3_IP}/reloadxmb.ps3")
    if reload_status != 200:
        return 502, {"ok": False, "reason": f"webMAN XMB reload returned HTTP {reload_status}"}
    return 200, {"ok": True, "action": "net-refresh-xmb"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/refresh" or (ALLOW_IP and self.client_address[0] != ALLOW_IP):
            self.send_error(403)
            return
        supplied = self.headers.get("X-PS3-Refresh-Token", "")
        if not TOKEN or not hmac.compare_digest(supplied, TOKEN):
            self.send_error(401)
            return
        status, payload = refresh()
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_: object) -> None:
        return


def main() -> int:
    host = os.environ.get("RELAY_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("RELAY_PORT", "38474"))
    if not ALLOW_IP:
        raise SystemExit("MANAGEMENT_ALLOW_IP is required")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
