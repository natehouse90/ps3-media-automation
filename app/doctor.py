from __future__ import annotations

import socket
import hashlib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Settings
from .storage import configured_storage_usages, human_size


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _write_check(name: str, path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check(name, "PASS", f"Writable: {path}")
    except OSError as exc:
        return Check(name, "FAIL", f"Not writable: {path} ({exc})", "Choose a writable directory or grant ownership to the application user; do not use chmod 777.")


def _http(name: str, url: str) -> Check:
    if not url:
        return Check(name, "WARNING", "Not configured", "Set the endpoint in Settings when this integration is available.")
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return Check(name, "PASS", f"HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            return Check(name, "PASS", f"Reachable (HTTP {exc.code})")
        return Check(name, "WARNING", f"HTTP {exc.code}")
    except (OSError, urllib.error.URLError) as exc:
        return Check(name, "FAIL", str(exc), "Check the host, port, firewall, and service configuration.")


def run(settings: Settings) -> list[dict[str, str]]:
    checks = [
        _write_check("state", settings.state_root),
        _write_check("incoming", settings.incoming_dir),
        _write_check("work", settings.work_root),
        _write_check("IRD root", settings.ird_root),
        _write_check("PS3ISO output", settings.iso_root),
        Check("makeps3iso", "PASS" if settings.makeps3iso.is_file() and (settings.makeps3iso.stat().st_mode & 0o111) else "FAIL", str(settings.makeps3iso), "Install the pinned trusted builder, verify its SHA-256, and set PS3_MAKEPS3ISO."),
        Check("IRD parser", "PASS" if settings.ird_parser_root.exists() else "FAIL", str(settings.ird_parser_root), "Mount or install the trusted IRD parser and set PS3_IRD_PARSER_ROOT."),
    ]
    if settings.ps3_ip:
        checks.append(_http("webMAN", f"http://{settings.ps3_ip}/index.ps3"))
    else:
        checks.append(Check("webMAN", "WARNING", "PS3 host not configured"))
    if settings.ps3netsrv_host:
        try:
            with socket.create_connection((settings.ps3netsrv_host, settings.ps3netsrv_port), timeout=3):
                checks.append(Check("ps3netsrv", "PASS", f"TCP {settings.ps3netsrv_host}:{settings.ps3netsrv_port}"))
        except OSError as exc:
            checks.append(Check("ps3netsrv", "FAIL", str(exc), "Check the host, port, and read-only library mount."))
    else:
        checks.append(Check("ps3netsrv", "WARNING", "Not configured"))
    checks.append(_http("Prowlarr", settings.prowlarr_url.rstrip("/") + "/api/v1/system/status" if settings.prowlarr_url else ""))
    if settings.makeps3iso.is_file():
        digest = hashlib.sha256(settings.makeps3iso.read_bytes()).hexdigest()
        checks.append(Check("makeps3iso checksum", "PASS" if digest == "c36fe8e6daf9c3ca3d617f79dc524a595aae4f178e52b314937f2fce5a9f48e4" else "FAIL", digest, "Install the pinned ps3iso-utils builder and verify its documented SHA-256."))
    storage = configured_storage_usages(settings)
    if storage:
        for usage in storage:
            labels = ", ".join(usage.labels)
            checks.append(Check(
                f"free space ({labels})",
                "PASS" if usage.free > 5 * 1024**3 else "WARNING",
                f"{human_size(usage.free)} free of {human_size(usage.total)} at {usage.path}",
                "Provide more working storage before reconstructing large titles.",
            ))
    else:
        checks.append(Check(
            "free space",
            "WARNING",
            "No configured PS3 storage path is visible",
            "Mount the PS3 storage filesystem and configure PS3_CAPACITY_PATHS.",
        ))
    return [asdict(item) for item in checks]
