#!/usr/bin/env python3
import os
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ps3-api-tests-"))
    try:
        os.environ.update({"APP_STATE_ROOT": str(root / "state"), "PS3_LIBRARY_ROOT": str(root / "library"), "PS3_INCOMING_DIR": str(root / "incoming"), "PS3_WORK_ROOT": str(root / "work"), "PS3_IRD_ROOT": str(root / "irds"), "PS3_ISO_ROOT": str(root / "iso"), "PS3_AUDIT_ROOT": str(root / "audits"), "PROWLARR_PUBLIC_URL": "http://example.invalid:9696"})
        from fastapi.testclient import TestClient
        from app import main as main_app
        main_app.settings.configured = True
        main_app.settings.ensure_dirs()
        (main_app.settings.iso_root / "Example Game [BLUS12345].iso").write_bytes(b"iso fixture")
        main_app.prowlarr_search = lambda _q, _limit: {
            "counts": {"PS3 indexer": 1, "Fallback indexer": 1},
            "results": [
                {"title": "Example PS3 Result", "indexer": "PS3 indexer", "indexerId": 1, "size": 1024, "age": 1, "categories": [{"name": "Console/PS3"}], "ps3SearchCategory": 1080},
                {"title": "Fallback Result", "indexer": "Fallback indexer", "indexerId": 2, "size": 2048, "age": 2, "categories": [], "ps3SearchCategory": None},
            ],
        }
        main_app.sab_queue = lambda: {"available": True, "status": "Downloading", "jobs": [{"id": "x", "title": "Active Game", "progress": 42, "speed": "5 MB/s", "eta": "00:10:00"}]}
        job_id = main_app.db.add_job(str(main_app.settings.incoming_dir / "Active Game"), "active", "PS3_FOLDER")
        main_app.db.update_job(job_id, state="AUDITING", stage="audit running")
        app = main_app.app
        with TestClient(app) as client:
            assert client.get("/healthz").json()["version"] == "0.2.3"
            home = client.get("/?q=example")
            assert home.status_code == 200
            text = home.text
            assert text.index("Search") < text.index("Downloads &amp; Processing") < text.index("Ready Library")
            assert "VALIDATING" in text and "5 MB/s" in text and "00:10:00" in text
            assert text.count("Active Game") == 1
            assert "Example Game" in text and "Open in Prowlarr" in text
            assert "indexerIds=1" in text and "categories=1080" in text
            fallback = text[text.index("Fallback Result"):]
            assert "indexerIds=2" in fallback and "categories=1080" not in fallback.split("</tr>", 1)[0]
            assert client.get("/search", follow_redirects=False).status_code == 307
            assert client.get("/library", follow_redirects=False).status_code == 307
            assert client.post("/search").status_code == 405
            assert client.get("/setup").status_code == 200
            assert client.get("/api/doctor").status_code == 200
        print("api tests: PASS")
        return 0
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
