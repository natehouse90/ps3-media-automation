#!/usr/bin/env python3
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import monitor
from app.monitor import parse_sab_queue


def main() -> int:
    result = parse_sab_queue({"queue": {"status": "Downloading", "speed": "8 MB/s", "timeleft": "00:05:00", "slots": [
        {"nzo_id": "one", "filename": "PS3 Game", "cat": "ps3", "status": "Downloading", "percentage": "37", "timeleft": "00:04:00"},
        {"nzo_id": "two", "filename": "TV Show", "cat": "tv", "percentage": "90"},
    ]}}, "ps3")
    assert result["available"] and len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["title"] == "PS3 Game" and job["progress"] == 37
    assert job["speed"] == "8 MB/s" and job["eta"] == "00:04:00"
    handed = parse_sab_queue({"queue": {"slots": [{"nzo_id": "three", "filename": "Handed Off PS3", "cat": "", "percentage": "12"}]}}, "ps3", {"handed off ps3"})
    assert len(handed["jobs"]) == 1 and handed["jobs"][0]["category"] == ""
    class FakeDB:
        def __init__(self): self.matched = []
        def pending_ps3_handoffs(self): return [{"id": 7, "normalized": "handed off ps3"}]
        def match_ps3_handoff(self, handoff_id, nzo_id): self.matched.append((handoff_id, nzo_id))
    calls = []
    monitor._request_queue = lambda *_args: {"queue": {"slots": [
        {"nzo_id": "n1", "filename": "Handed Off PS3", "cat": ""},
        {"nzo_id": "n2", "filename": "Unrelated TV", "cat": "tv"},
    ]}}
    monitor._change_category = lambda *_args: calls.append(_args[3:]) or True
    os.environ.update({"SAB_URL": "http://sab", "SAB_API_KEY": "test-key"})
    fake = FakeDB()
    assert monitor.reconcile_ps3_handoffs(fake) == {"handed off ps3"}
    assert fake.matched == [(7, "n1")] and len(calls) == 1
    os.environ["SAB_HOST_HEADER"] = "sab.internal"
    print("monitor tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
