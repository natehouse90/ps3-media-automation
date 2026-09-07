from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
import re

from .config import Settings
from .db import Database


class Worker:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name="ps3-ingest-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    @staticmethod
    def fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        entries = []
        if path.is_file():
            stat = path.stat()
            entries.append((path.name, stat.st_size, stat.st_mtime_ns))
        else:
            for child in path.rglob("*"):
                if child.is_file() and not child.is_symlink():
                    stat = child.stat()
                    entries.append((str(child.relative_to(path)), stat.st_size, stat.st_mtime_ns))
        for item in sorted(entries):
            digest.update(json.dumps(item, separators=(",", ":")).encode())
        return digest.hexdigest()

    def stable(self, path: Path) -> bool:
        first = self.fingerprint(path)
        time.sleep(max(0, self.settings.stable_seconds))
        return first == self.fingerprint(path)

    @staticmethod
    def ps3_folder(path: Path) -> Path | None:
        if path.is_dir() and (path / "PS3_GAME" / "PARAM.SFO").is_file():
            return path
        if path.is_dir():
            matches = [item.parent.parent for item in path.rglob("PARAM.SFO") if item.parent.name.upper() == "PS3_GAME"]
            return matches[0] if len(matches) == 1 else None
        return None

    def discover(self) -> None:
        if not self.settings.incoming_dir.exists():
            return
        for candidate in sorted(self.settings.incoming_dir.iterdir()):
            if candidate.name.startswith(".") or candidate.name.endswith((".part", ".partial", ".tmp", ".nzb")):
                continue
            source_type = "PS3_FOLDER" if self.ps3_folder(candidate) else "OTHER"
            self.db.add_job(str(candidate), self.fingerprint(candidate), source_type)

    def process_one(self, job: dict) -> None:
        source = Path(job["source"])
        if not source.exists() or not self.stable(source):
            return
        job_id = job["id"]
        self.db.update_job(job_id, state="IDENTIFYING", stage="identifying")
        if job["source_type"] != "PS3_FOLDER":
            command = [str(Path(self.settings.ingest_command).with_name("ps3-import-incoming.py")), "--source", str(source)]
            env = os.environ.copy()
            env.update({"PS3_LIBRARY_ROOT": str(self.settings.library_root), "PS3_INCOMING_DIR": str(self.settings.incoming_dir), "PS3_ISO_ROOT": str(self.settings.iso_root)})
            result = subprocess.run(command, text=True, capture_output=True, timeout=None, env=env)
            if result.returncode == 0 and "imported=0" not in result.stdout:
                self.db.update_job(job_id, state="READY", stage="routed", reason="Existing importer routed this non-PS3 item; source preserved.")
            else:
                self.db.update_job(job_id, state="NEEDS_ATTENTION", stage="manual-format-routing", reason="Existing importer could not classify this item; the source was preserved.")
            return
        self.db.update_job(job_id, state="AUDITING", stage="IRD preflight")
        command = [self.settings.ingest_command, str(self.ps3_folder(source) or source)]
        env = os.environ.copy()
        env.update({"PS3_LIBRARY_ROOT": str(self.settings.library_root), "PS3_INCOMING_DIR": str(self.settings.incoming_dir), "PS3_WORK_ROOT": str(self.settings.work_root), "PS3_IRD_ROOT": str(self.settings.ird_root), "PS3_ISO_ROOT": str(self.settings.iso_root), "PS3_AUDIT_ROOT": str(self.settings.audit_root), "PS3_MAKEPS3ISO": str(self.settings.makeps3iso)})
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=None, env=env)
        except OSError as exc:
            self.db.update_job(job_id, state="FAILED", stage="execution", reason=str(exc))
            return
        detail = (completed.stdout + "\n" + completed.stderr).strip()[-4000:]
        match = re.search(r"\b[A-Z]{4}\d{5}\b", detail + " " + source.name, re.I)
        if match:
            self.db.update_job(job_id, title_id=match.group(0).upper())
        if completed.returncode != 0:
            state = "WAITING_FOR_IRD" if "IRD" in detail.upper() and ("missing" in detail.lower() or "no trusted" in detail.lower()) else "FAILED"
            self.db.update_job(job_id, state=state, stage="validation", reason=detail or "Ingest failed closed")
            self.db.log("ERROR", f"job {job_id}: {detail[-500:]}")
            return
        iso = next((str(p) for p in self.settings.iso_root.glob("*.iso") if p.is_file()), "")
        reason = "Verified ISO published; source preserved"
        if os.environ.get("PS3_REFRESH_RELAY_URL"):
            refresh = subprocess.run([self.settings.refresh_command, "--xmb"], text=True, capture_output=True, env=env, timeout=60)
            if refresh.returncode == 3:
                self.db.update_job(job_id, state="WAITING_FOR_PS3_IDLE", stage="refresh deferred", reason="ISO is ready; XMB refresh pending because gameplay was preserved.")
                return
            if refresh.returncode != 0:
                reason = "Verified ISO published; webMAN refresh pending"
        self.db.update_job(job_id, state="READY", stage="published", iso_path=iso, reason=reason)
        self.db.log("INFO", f"job {job_id}: verified ISO published")

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.settings.load_persisted().ensure_dirs()
                if os.environ.get("PS3_REFRESH_RELAY_URL"):
                    pending = os.environ.get("PS3_REFRESH_PENDING_FILE", str(self.settings.state_root / "refresh-pending.json"))
                    if Path(pending).exists():
                        subprocess.run([self.settings.refresh_command, "--xmb"], capture_output=True, timeout=60, env=os.environ.copy())
                self.discover()
                for job in self.db.jobs(100):
                    if job["state"] in ("QUEUED", "IDENTIFYING", "AUDITING", "RECONSTRUCTING", "BUILDING_ISO", "VERIFYING", "PUBLISHING"):
                        self.process_one(job)
            except Exception as exc:
                self.db.log("ERROR", f"worker: {exc}")
            self.stop_event.wait(max(1, self.settings.worker_interval))
