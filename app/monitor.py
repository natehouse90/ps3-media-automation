from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


def normalized_title(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def parse_sab_queue(payload: dict[str, Any], category: str = "ps3", allowed_normalized: set[str] | None = None) -> dict[str, Any]:
    queue = payload.get("queue", payload)
    slots = queue.get("slots", []) if isinstance(queue, dict) else []
    wanted = category.casefold()
    jobs: list[dict[str, Any]] = []
    for slot in slots if isinstance(slots, list) else []:
        if not isinstance(slot, dict):
            continue
        filename = str(slot.get("filename") or "PS3 download")
        is_ps3 = str(slot.get("cat", "")).casefold() == wanted
        is_handoff = allowed_normalized is not None and normalized_title(filename) in allowed_normalized
        if not is_ps3 and not is_handoff:
            continue
        try:
            progress = max(0.0, min(100.0, float(slot.get("percentage", 0) or 0)))
        except (TypeError, ValueError):
            progress = 0.0
        jobs.append({
            "id": str(slot.get("nzo_id", "")),
            "title": filename, "category": str(slot.get("cat") or ""),
            "status": str(slot.get("status") or "Downloading"),
            "progress": progress,
            "speed": str(slot.get("speed") or queue.get("speed") or "0 B/s"),
            "eta": str(slot.get("timeleft") or queue.get("timeleft") or "Unknown"),
        })
    return {"available": True, "status": str(queue.get("status", "Unknown")), "jobs": jobs}


def _request_queue(url: str, api_key: str, host_header: str, timeout: float) -> dict[str, Any]:
    body = urllib.parse.urlencode({"mode": "queue", "output": "json", "apikey": api_key}).encode()
    headers = {"Host": host_header} if host_header else {}
    request = urllib.request.Request(url + "/api", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _change_category(url: str, api_key: str, host_header: str, nzo_id: str, timeout: float) -> bool:
    body = urllib.parse.urlencode({"mode": "change_cat", "nzo_ids": nzo_id, "value": "ps3", "output": "json", "apikey": api_key}).encode()
    headers = {"Host": host_header} if host_header else {}
    request = urllib.request.Request(url + "/api", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        return payload.get("status") in (True, "OK", "ok") if isinstance(payload, dict) else True
    except Exception:
        return False


def reconcile_ps3_handoffs(db: Any, timeout: float = 2.5) -> set[str]:
    """Tag only exact, recent PS3-app handoff matches; never touch other jobs."""
    url = os.environ.get("SAB_URL", "").strip().rstrip("/")
    api_key = os.environ.get("SAB_API_KEY", "").strip()
    if not url or not api_key:
        return set()
    pending = db.pending_ps3_handoffs()
    if not pending:
        return set()
    host_header = os.environ.get("SAB_HOST_HEADER", "").strip()
    try:
        payload = _request_queue(url, api_key, host_header, timeout)
    except Exception:
        return set()
    queue = payload.get("queue", payload) if isinstance(payload, dict) else {}
    matched: set[str] = set()
    for slot in queue.get("slots", []) if isinstance(queue, dict) else []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("cat", "")).casefold() == "ps3":
            continue
        filename = normalized_title(str(slot.get("filename") or ""))
        for handoff in pending:
            if filename and filename == str(handoff.get("normalized") or ""):
                nzo_id = str(slot.get("nzo_id") or "")
                if nzo_id and _change_category(url, api_key, host_header, nzo_id, timeout):
                    db.match_ps3_handoff(int(handoff["id"]), nzo_id)
                    matched.add(filename)
                break
    return matched


def sab_queue(db: Any = None, timeout: float = 2.5) -> dict[str, Any]:
    url = os.environ.get("SAB_URL", "").strip().rstrip("/")
    api_key = os.environ.get("SAB_API_KEY", "").strip()
    category = os.environ.get("SAB_CATEGORY", "ps3").strip() or "ps3"
    if not url or not api_key:
        return {"available": False, "status": "Not configured", "jobs": []}
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return {"available": False, "status": "Invalid SAB URL", "jobs": []}
    host_header = os.environ.get("SAB_HOST_HEADER", "").strip()
    try:
        allowed = reconcile_ps3_handoffs(db, timeout) if db is not None else set()
        payload = _request_queue(url, api_key, host_header, timeout)
        return parse_sab_queue(payload, category, allowed)
    except Exception:
        return {"available": False, "status": "Unavailable", "jobs": []}
