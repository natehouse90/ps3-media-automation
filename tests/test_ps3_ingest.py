#!/usr/bin/env python3
"""Small destructive-fixture tests for ps3_ingest; production games are never touched."""

import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import shutil
import sys
import tempfile
from pathlib import Path

MODULE = Path("/usr/local/sbin/ps3-ingest")
if not MODULE.exists():
    MODULE = Path(__file__).with_name("ps3_ingest.py")
spec = importlib.util.spec_from_loader("ps3_ingest", SourceFileLoader("ps3_ingest", str(MODULE)))
ingest = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ingest
spec.loader.exec_module(ingest)


def md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def info(path: str, data: bytes):
    return ingest.FileInfo(path, ingest.normalize(path), len(data), md5(data))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ps3-ingest-tests-"))
    results = {}
    try:
        payload = b"known-retail-payload"
        expected = {"payload.bin": {"path": "payload.bin", "size": len(payload), "md5": md5(payload)}}

        source = root / "rename-source"
        source.mkdir()
        (source / "payload.bin.any-extension").write_bytes(payload)
        before = hashlib.sha256((source / "payload.bin.any-extension").read_bytes()).hexdigest()
        files = {"payload.bin.any-extension": info("payload.bin.any-extension", payload)}
        classified = ingest.classify(files, expected)
        work = root / "rename-work"
        shutil.copytree(source, work)
        actions = ingest.reconstruct(work, classified)
        after = hashlib.sha256((source / "payload.bin.any-extension").read_bytes()).hexdigest()
        results["synthetic_rename"] = len(classified["renamed"]) == 1 and (work / "payload.bin").read_bytes() == payload and before == after and bool(actions)

        corrupt = b"known-retail-payloae"
        bad = ingest.classify({"payload.bin.any-extension": info("payload.bin.any-extension", corrupt)}, expected)
        results["bad_content_fail_closed"] = len(bad["renamed"]) == 0 and len(bad["missing"]) == 1 and len(bad["extra"]) == 1

        extra_files = {"payload.bin": info("payload.bin", payload), "SCENE.NFO": info("SCENE.NFO", b"junk")}
        extra = ingest.classify(extra_files, expected)
        results["extra_reported"] = [row["actual"] for row in extra["extra"]] == ["SCENE.NFO"]

        collision = root / "collision.iso"
        collision.write_bytes(b"existing")
        try:
            ingest.refuse_existing_iso(collision)
            collision_refused = False
        except ingest.PreflightError:
            collision_refused = collision.read_bytes() == b"existing"
        results["collision_refused"] = collision_refused

        prior_root = root / "audits"
        prior_root.mkdir()
        ingest.AUDIT_ROOT = prior_root
        validated = root / "validated.iso"
        validated.write_bytes(b"validated-image")
        fingerprint = "a" * 64
        prior = {"source_fingerprint": fingerprint, "final_validation": "PASS", "iso_path": str(validated), "iso_sha256": ingest.hash_file(validated, "sha256"), "timestamp": "prior"}
        (prior_root / "prior.json").write_text(json.dumps(prior), encoding="utf-8")
        results["rerun_reuses_validated"] = ingest.find_prior({"source_fingerprint": fingerprint}, validated) == prior

        print(json.dumps(results, sort_keys=True))
        return 0 if all(results.values()) else 1
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
