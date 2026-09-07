from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


def parse_sab_queue(payload: dict[str, Any], category: str = "ps3") -> dict[str, Any]:
    queue = payload.get("queue", payload)
    slots = queue.get("slots", []) if isinstance(queue, dict) else []
    wanted = category.casefold()
    jobs: list[dict[str, Any]] = []
    for slot in slots if isinstance(slots, list) else []:
        if not isinstance(slot, dict) or str(slot.get("cat", "")).casefold() != wanted:
            continue
        try:
            progress = max(0.0, min(100.0, float(slot.get("percentage", 0) or 0)))
        except (TypeError, ValueError):
            progress = 0.0
        jobs.append({
            "id": str(slot.get("nzo_id", "")),
            "title": str(slot.get("filename") or "PS3 download"),
            "status": str(slot.get("status") or "Downloading"),
            "progress": progress,
            "speed": str(slot.get("speed") or queue.get("speed") or "0 B/s"),
            "eta": str(slot.get("timeleft") or queue.get("timeleft") or "Unknown"),
        })
    return {"available": True, "status": str(queue.get("status", "Unknown")), "jobs": jobs}


def sab_queue(timeout: float = 2.5) -> dict[str, Any]:
    url = os.environ.get("SAB_URL", "").strip().rstrip("/")
    api_key = os.environ.get("SAB_API_KEY", "").strip()
    category = os.environ.get("SAB_CATEGORY", "ps3").strip() or "ps3"
    if not url or not api_key:
        return {"available": False, "status": "Not configured", "jobs": []}
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return {"available": False, "status": "Invalid SAB URL", "jobs": []}
    body = urllib.parse.urlencode({"mode": "queue", "output": "json", "apikey": api_key}).encode()
    headers = {}
    host_header = os.environ.get("SAB_HOST_HEADER", "").strip()
    if host_header:
        headers["Host"] = host_header
    request = urllib.request.Request(url + "/api", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        return parse_sab_queue(payload, category)
    except Exception:
        return {"available": False, "status": "Unavailable", "jobs": []}
