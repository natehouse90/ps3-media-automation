#!/usr/bin/env python3
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

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
    os.environ["SAB_HOST_HEADER"] = "sab.internal"
    print("monitor tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
