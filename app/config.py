from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


@dataclass
class Settings:
    state_root: Path = field(default_factory=lambda: _path("APP_STATE_ROOT", "/var/lib/ps3-media-automation"))
    library_root: Path = field(default_factory=lambda: _path("PS3_LIBRARY_ROOT", "/srv/ps3-library"))
    incoming_dir: Path = field(default_factory=lambda: _path("PS3_INCOMING_DIR", "/srv/ps3-library/incoming"))
    work_root: Path = field(default_factory=lambda: _path("PS3_WORK_ROOT", "/srv/ps3-library/.iso-build-work"))
    ird_root: Path = field(default_factory=lambda: _path("PS3_IRD_ROOT", "/srv/ps3-library/.ingest/irds"))
    iso_root: Path = field(default_factory=lambda: _path("PS3_ISO_ROOT", "/srv/ps3-library/PS3ISO"))
    audit_root: Path = field(default_factory=lambda: _path("PS3_AUDIT_ROOT", "/srv/ps3-library/.ingest/audits"))
    ingest_command: str = os.environ.get("PS3_INGEST_COMMAND", "/opt/ps3-media-automation/scripts/ps3_ingest.py")
    refresh_command: str = os.environ.get("PS3_REFRESH_COMMAND", "/opt/ps3-media-automation/scripts/ps3-refresh.py")
    makeps3iso: Path = field(default_factory=lambda: _path("PS3_MAKEPS3ISO", "/opt/ps3-tools/makeps3iso"))
    ird_parser_root: Path = field(default_factory=lambda: _path("PS3_IRD_PARSER_ROOT", "/opt/ps3-tools/pyird"))
    ps3_ip: str = os.environ.get("PS3_IP", "")
    ps3netsrv_host: str = os.environ.get("PS3NETSRV_HOST", "")
    ps3netsrv_port: int = int(os.environ.get("PS3NETSRV_PORT", "38008"))
    prowlarr_url: str = os.environ.get("PROWLARR_URL", "")
    prowlarr_api_key: str = os.environ.get("PROWLARR_API_KEY", "")
    worker_interval: int = int(os.environ.get("WORKER_INTERVAL_SECONDS", "5"))
    stable_seconds: int = int(os.environ.get("INGEST_STABLE_SECONDS", "30"))
    configured: bool = False

    @property
    def db_path(self) -> Path:
        return self.state_root / "state.sqlite3"

    @property
    def config_path(self) -> Path:
        return self.state_root / "config.json"

    def load_persisted(self) -> "Settings":
        if not self.config_path.exists():
            return self
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self
        for key, value in data.items():
            if key == "prowlarr_api_key":
                continue
            if hasattr(self, key) and value not in (None, ""):
                current = getattr(self, key)
                setattr(self, key, Path(value) if isinstance(current, Path) else value)
        self.prowlarr_api_key = os.environ.get("PROWLARR_API_KEY", self.prowlarr_api_key)
        self.configured = bool(data.get("configured", False))
        return self

    def save(self, values: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        safe: dict[str, Any] = {"configured": True}
        for key in ("library_root", "incoming_dir", "work_root", "ird_root", "iso_root", "audit_root", "ps3_ip", "ps3netsrv_host", "ps3netsrv_port", "prowlarr_url"):
            if key in values and values[key] not in (None, ""):
                safe[key] = str(values[key])
                current = getattr(self, key)
                setattr(self, key, Path(values[key]) if isinstance(current, Path) else values[key])
        if values.get("prowlarr_api_key"):
            self.prowlarr_api_key = str(values["prowlarr_api_key"])
            safe["prowlarr_api_key_set"] = True
        self.config_path.write_text(json.dumps(safe, indent=2) + "\n", encoding="utf-8")
        try:
            self.config_path.chmod(0o600)
        except OSError:
            pass
        self.configured = True

    def ensure_dirs(self) -> None:
        for path in (self.state_root, self.incoming_dir, self.work_root, self.ird_root, self.iso_root, self.audit_root):
            path.mkdir(parents=True, exist_ok=True)


def csrf_token() -> str:
    return secrets.token_urlsafe(24)
