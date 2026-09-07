from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class StorageUsage:
    labels: tuple[str, ...]
    path: Path
    total: int
    used: int
    free: int
    device: int


def human_size(value: int | float) -> str:
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} PiB"


def configured_storage_usages(settings: Settings) -> list[StorageUsage]:
    configured = os.environ.get("PS3_CAPACITY_PATHS", "").strip()
    candidates: list[tuple[str, Path]] = []
    if configured:
        for entry in configured.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if "=" in entry:
                label, raw_path = entry.split("=", 1)
            else:
                label, raw_path = "Storage", entry
            candidates.append((label.strip() or "Storage", Path(raw_path.strip())))
    else:
        candidates = [
            ("Incoming", settings.incoming_dir),
            ("Work", settings.work_root),
            ("PS3ISO", settings.iso_root),
        ]

    grouped: dict[int, dict[str, object]] = {}
    for label, path in candidates:
        try:
            device = path.stat().st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        current = grouped.get(device)
        if current is None:
            grouped[device] = {
                "labels": [label],
                "path": path,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            }
        else:
            labels = current["labels"]
            if isinstance(labels, list) and label not in labels:
                labels.append(label)

    return [
        StorageUsage(
            labels=tuple(item["labels"]),
            path=item["path"],
            total=int(item["total"]),
            used=int(item["used"]),
            free=int(item["free"]),
            device=device,
        )
        for device, item in grouped.items()
    ]
