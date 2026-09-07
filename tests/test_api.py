#!/usr/bin/env python3
import os
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ps3-api-tests-"))
    try:
        os.environ.update({"APP_STATE_ROOT": str(root / "state"), "PS3_LIBRARY_ROOT": str(root / "library"), "PS3_INCOMING_DIR": str(root / "incoming"), "PS3_WORK_ROOT": str(root / "work"), "PS3_IRD_ROOT": str(root / "irds"), "PS3_ISO_ROOT": str(root / "iso"), "PS3_AUDIT_ROOT": str(root / "audits")})
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            assert client.get("/healthz").json()["version"] == "0.2.0"
            assert client.get("/", follow_redirects=False).status_code == 307
            assert client.get("/setup").status_code == 200
            assert client.get("/api/doctor").status_code == 200
        print("api tests: PASS")
        return 0
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
