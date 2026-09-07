#!/usr/bin/env python3
import tempfile
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.config import Settings
from app.db import Database
from app.doctor import run as doctor
from app.worker import Worker


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ps3-app-tests-"))
    try:
        settings = Settings(state_root=root / "state", library_root=root / "library", incoming_dir=root / "incoming", work_root=root / "work", ird_root=root / "irds", iso_root=root / "iso", audit_root=root / "audits", stable_seconds=0)
        settings.ensure_dirs()
        settings.save({"library_root": settings.library_root, "incoming_dir": settings.incoming_dir, "work_root": settings.work_root, "ird_root": settings.ird_root, "iso_root": settings.iso_root})
        assert settings.config_path.exists()
        if os.name != "nt":
            assert settings.config_path.stat().st_mode & 0o077 == 0
        db = Database(settings.db_path)
        job_id = db.add_job(str(settings.incoming_dir / "demo"), "fingerprint", "PS3_FOLDER")
        assert job_id and db.get_job(job_id)["state"] == "QUEUED"
        db.update_job(job_id, state="READY", stage="published")
        assert db.counts()["READY"] == 1
        worker = Worker(settings, db)
        source = settings.incoming_dir / "source"
        (source / "PS3_GAME").mkdir(parents=True)
        (source / "PS3_GAME" / "PARAM.SFO").write_bytes(b"fixture")
        assert worker.ps3_folder(source) == source and worker.stable(source)
        assert any(item["name"] == "incoming" for item in doctor(settings))
        print("app tests: PASS")
        return 0
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
