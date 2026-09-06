#!/usr/bin/env python3
"""Run two Prowlarr-backed PS3 searches with different category capabilities.

The configured PS3-capable indexer receives category 1080. The configured
fallback indexer is queried without a category. This command never grabs or
downloads a result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any


PROWLARR_CONTAINER = os.environ.get("PROWLARR_CONTAINER", "prowlarr")
PROWLARR_URL = os.environ.get("PROWLARR_SEARCH_URL", "http://127.0.0.1:9696/api/v1/search")
INDEXERS = (
    (int(os.environ.get("PROWLARR_PS3_INDEXER_ID", "1")), os.environ.get("PROWLARR_PS3_INDEXER_NAME", "PS3-category indexer"), int(os.environ.get("PROWLARR_PS3_CATEGORY", "1080"))),
    (int(os.environ.get("PROWLARR_NOCATEGORY_INDEXER_ID", "2")), os.environ.get("PROWLARR_NOCATEGORY_INDEXER_NAME", "no-category indexer"), None),
)


def docker_prefix() -> list[str]:
    if os.geteuid() == 0:
        return ["docker"]
    return ["sudo", "-n", "docker"]


def container_config() -> str:
    result = subprocess.run(
        docker_prefix() + ["exec", PROWLARR_CONTAINER, "cat", "/config/config.xml"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("cannot read the Prowlarr container configuration")
    return result.stdout


def api_key() -> str:
    configured = os.environ.get("PROWLARR_API_KEY", "").strip()
    if configured:
        return configured
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", container_config())
    if not match:
        raise RuntimeError("Prowlarr API key was not found")
    return match.group(1).strip()


def search(term: str, indexer_id: int, category: int | None, limit: int) -> list[dict[str, Any]]:
    # curl runs inside media-prowlarr so both API access and indexer traffic use
    # the existing Prowlarr/Gluetun path.
    args = docker_prefix() + [
        "exec", PROWLARR_CONTAINER, "curl", "-fsS", "--max-time", "120",
        "-H", "X-Api-Key: " + api_key(), "--get", PROWLARR_URL,
        "--data-urlencode", "query=" + term,
        "--data-urlencode", "indexerIds=" + str(indexer_id),
        "--data-urlencode", "type=search",
        "--data-urlencode", "limit=" + str(limit),
        "--data-urlencode", "offset=0",
    ]
    if category is not None:
        args += ["--data-urlencode", "categories=" + str(category)]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        raise RuntimeError(f"Prowlarr search failed for indexer {indexer_id}: {' '.join(detail)}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Prowlarr returned invalid search JSON for indexer {indexer_id}") from exc
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("data", []))
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected Prowlarr result shape for indexer {indexer_id}")
    return [x for x in payload if isinstance(x, dict)]


def normalized_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def dedupe(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    merged: list[dict[str, Any]] = []
    for item in results:
        title = str(item.get("title") or "")
        size = int(item.get("size") or 0)
        key = (normalized_title(title), size)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def value(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return default


def format_size(size: Any) -> str:
    try:
        n = float(size)
    except (TypeError, ValueError):
        return "?"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.1f} {unit}"
        n /= 1024
    return "?"


def format_date(raw: Any) -> str:
    if not raw:
        return "?"
    text = str(raw)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return text[:10]


def main() -> int:
    parser = argparse.ArgumentParser(prog="ps3-search")
    parser.add_argument("term", help="PS3 search terms")
    parser.add_argument("--limit", type=int, default=100, help="maximum results per indexer (default: 100)")
    parser.add_argument("--json", action="store_true", help="emit merged JSON results")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")

    all_results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for indexer_id, name, category in INDEXERS:
        raw = search(args.term, indexer_id, category, args.limit)
        counts[name] = len(raw)
        for item in raw:
            item = dict(item)
            item.setdefault("indexer", name)
            item["ps3SearchCategory"] = category
            all_results.append(item)

    merged = dedupe(all_results)
    if args.json:
        print(json.dumps({"query": args.term, "counts": counts, "results": merged}, indent=2, sort_keys=True))
        return 0

    print(f"PS3 SEARCH: {args.term}")
    for _, name, category in INDEXERS:
        print(f"{name}: category={category if category is not None else 'none'} results={counts[name]}")
    print(f"Merged unique results: {len(merged)}")
    for number, item in enumerate(merged, 1):
        title = value(item, "title", default="(untitled)")
        name = value(item, "indexer", default="unknown")
        date = format_date(value(item, "publishDate", "releaseDate"))
        age = value(item, "age", "ageHours", default="?")
        size = format_size(value(item, "size", default=0))
        categories = value(item, "categories", default=[])
        if isinstance(categories, list):
            categories = ",".join(str(x.get("id", x)) if isinstance(x, dict) else str(x) for x in categories)
        guid = value(item, "guid", "id", default="?")
        print(f"{number:03d} [{name}] {title} | date={date} age={age} size={size} categories={categories or '-'} guid={guid}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except RuntimeError as exc:
        print(f"ps3-search: {exc}", file=sys.stderr)
        raise SystemExit(1)
