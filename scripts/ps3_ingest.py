#!/usr/bin/env python3
"""Fail-closed, source-immutable PS3 folder ingest and ISO reconstruction."""

from __future__ import annotations

import argparse
try:
    import fcntl
except ImportError:  # pragma: no cover - CLI locking is a Linux deployment feature
    fcntl = None
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
ROOT = Path(os.environ.get("PS3_LIBRARY_ROOT", "/srv/ps3-library"))
WORK_ROOT = Path(os.environ.get("PS3_WORK_ROOT", str(ROOT / ".iso-build-work")))
STATE_ROOT = Path(os.environ.get("PS3_STATE_ROOT", str(ROOT / ".ingest")))
IRD_ROOT = Path(os.environ.get("PS3_IRD_ROOT", str(STATE_ROOT / "irds")))
AUDIT_ROOT = Path(os.environ.get("PS3_AUDIT_ROOT", str(STATE_ROOT / "audits")))
KNOWN_ISOS = Path(os.environ.get("PS3_KNOWN_ISOS", str(STATE_ROOT / "known-isos.json")))
LOCK_PATH = Path(os.environ.get("PS3_INGEST_LOCK", str(STATE_ROOT / "ps3-ingest.lock")))
ISO_ROOT = Path(os.environ.get("PS3_ISO_ROOT", str(ROOT / "PS3ISO")))
MAKEPS3ISO = Path(os.environ.get("PS3_MAKEPS3ISO", "/usr/local/lib/ps3-ingest/bin/makeps3iso"))
VENDOR = Path(os.environ.get("PS3_IRD_PARSER_ROOT", "/usr/local/lib/ps3-ingest/vendor/pyird"))
TOOL_RELEASE = "ps3iso-utils 277db7de (Ubuntu release)"
TOOL_SOURCE = "https://github.com/bucanero/ps3iso-utils/releases/tag/277db7de"
TOOL_SHA256 = "c36fe8e6daf9c3ca3d617f79dc524a595aae4f178e52b314937f2fce5a9f48e4"
ALLOWED_ISO_OMISSIONS = {"ps3_update/ps3updat.pup"}
ALLOWED_SOURCE_OMISSIONS = {"ps3_update/ps3updat.pup"}
TITLE_ID_RE = re.compile(r"[A-Z]{4}-?\d{5}", re.I)


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileInfo:
    path: str
    norm: str
    size: int
    md5: str


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def normalize(path: str) -> str:
    return path.replace("\\", "/").strip("/").lower()


def hash_file(path: Path, algorithm: str = "md5") -> str:
    digest = hashlib.new(algorithm, usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sfo(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) < 20 or raw[:4] != b"\x00PSF":
        raise PreflightError(f"invalid PARAM.SFO: {path}")
    _, key_start, data_start, count = struct.unpack_from("<4xIIII", raw, 0)
    result: dict[str, Any] = {}
    for index in range(count):
        pos = 20 + index * 16
        if pos + 16 > len(raw):
            raise PreflightError("truncated PARAM.SFO index")
        key_off, fmt, length, _maximum, data_off = struct.unpack_from("<HHIII", raw, pos)
        key_pos = key_start + key_off
        key_end = raw.find(b"\x00", key_pos)
        if key_end < 0:
            raise PreflightError("invalid PARAM.SFO key table")
        key = raw[key_pos:key_end].decode("utf-8", "replace")
        value = raw[data_start + data_off:data_start + data_off + length]
        if fmt == 0x0404 and len(value) >= 4:
            result[key] = struct.unpack_from("<I", value, 0)[0]
        else:
            result[key] = value.rstrip(b"\x00").decode("utf-8", "replace")
    return result


def canonical_title_id(value: str) -> str:
    match = TITLE_ID_RE.search(value or "")
    return match.group(0).replace("-", "").upper() if match else ""


def find_game_root(source: Path) -> Path:
    source = source.resolve(strict=True)
    candidates: list[Path] = []
    if (source / "PS3_GAME" / "PARAM.SFO").is_file():
        candidates.append(source)
    else:
        candidates = sorted({p.parent.parent for p in source.rglob("PARAM.SFO") if p.parent.name.upper() == "PS3_GAME"})
    if len(candidates) != 1:
        raise PreflightError(f"expected exactly one PS3 game root; found {len(candidates)}")
    root = candidates[0].resolve()
    if root != source and source not in root.parents:
        raise PreflightError("identified game root escapes source")
    return root


def inventory(root: Path) -> tuple[dict[str, FileInfo], int, int, int, str]:
    files: dict[str, FileInfo] = {}
    directories = 1
    total_bytes = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        base_path = Path(base)
        for name in dirs:
            path = base_path / name
            if path.is_symlink():
                raise PreflightError(f"source contains symlink: {path.relative_to(root)}")
            directories += 1
        for name in names:
            path = base_path / name
            if path.is_symlink() or not path.is_file():
                raise PreflightError(f"source contains non-regular file: {path.relative_to(root)}")
            rel = path.relative_to(root).as_posix()
            norm = normalize(rel)
            if norm in files:
                raise PreflightError(f"case-insensitive path collision: {rel} and {files[norm].path}")
            size = path.stat().st_size
            info = FileInfo(rel, norm, size, hash_file(path))
            files[norm] = info
            total_bytes += size
    fingerprint = hashlib.sha256()
    for info in sorted(files.values(), key=lambda item: item.norm):
        fingerprint.update(f"{info.norm}\0{info.size}\0{info.md5}\n".encode())
    return files, len(files), directories, total_bytes, fingerprint.hexdigest()


def locate_ird(title_id: str, explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).resolve(strict=True)
        return path
    if not IRD_ROOT.exists():
        return None
    matches = []
    for path in IRD_ROOT.rglob("*.ird"):
        name_id = canonical_title_id(path.name)
        if name_id == title_id:
            matches.append(path)
    if not matches:
        return None
    unique = {hash_file(path, "sha256"): path for path in matches}
    if len(unique) != 1:
        raise PreflightError(f"multiple distinct IRDs found for {title_id}; specify --ird")
    return next(iter(unique.values()))


def load_ird(path: Path) -> tuple[Any, dict[str, dict[str, Any]], str]:
    sys.path.insert(0, str(VENDOR))
    try:
        from core.ird import parse_ird_content
    except Exception as exc:
        raise PreflightError(f"pinned IRD parser unavailable: {exc}") from exc
    raw = path.read_bytes()
    try:
        raw = gzip.decompress(raw)
    except (gzip.BadGzipFile, OSError):
        pass
    ird = parse_ird_content(raw)
    offsets = {entry["first_extent"]: entry for entry in ird.iso_files}
    expected: dict[str, dict[str, Any]] = {}
    for item in ird.files:
        entry = offsets.get(item.offset)
        if not entry:
            raise PreflightError(f"IRD has no path for sector {item.offset}")
        rel = entry["name"].replace("\\", "/").strip("/")
        norm = normalize(rel)
        if norm in expected:
            raise PreflightError(f"IRD contains duplicate path: {rel}")
        expected[norm] = {
            "path": rel,
            "md5": item.md5_checksum.hex(),
            "size": sum(int(size) for _sector, size in entry.get("extents", [])),
        }
    return ird, expected, hash_file(path, "sha256")


def classify(files: dict[str, FileInfo], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    exact, renamed, modified, missing, ambiguous = [], [], [], [], []
    consumed: set[str] = set()
    expected_norms = set(expected)
    hash_index: dict[str, list[FileInfo]] = {}
    for info in files.values():
        hash_index.setdefault(info.md5, []).append(info)
    for norm, target in expected.items():
        current = files.get(norm)
        if current:
            consumed.add(norm)
            row = {"expected": target["path"], "actual": current.path, "size": current.size, "md5": current.md5}
            if current.md5 == target["md5"] and current.size == target["size"]:
                exact.append(row)
            else:
                row.update({"expected_md5": target["md5"], "expected_size": target["size"]})
                modified.append(row)
            continue
        candidates = [item for item in hash_index.get(target["md5"], []) if item.norm not in expected_norms and item.norm not in consumed and item.size == target["size"]]
        if len(candidates) > 1:
            # Duplicate retail payloads can share a hash. A unique actual path made
            # from the complete expected path plus an appended suffix is safe only
            # when the IRD hash and size also match; suffix similarity alone is never used.
            appended = [item for item in candidates if item.norm.startswith(norm + ".")]
            if len(appended) == 1:
                candidates = appended
        if len(candidates) == 1:
            item = candidates[0]
            consumed.add(item.norm)
            renamed.append({"expected": target["path"], "actual": item.path, "size": item.size, "md5": item.md5})
        elif len(candidates) > 1:
            ambiguous.append({"expected": target["path"], "candidates": [item.path for item in candidates], "md5": target["md5"]})
        else:
            missing.append({"expected": target["path"], "size": target["size"], "md5": target["md5"]})
    extras = [{"actual": info.path, "size": info.size, "md5": info.md5} for norm, info in files.items() if norm not in consumed]
    return {"exact": exact, "renamed": renamed, "modified": modified, "missing": missing, "ambiguous": ambiguous, "extra": sorted(extras, key=lambda x: x["actual"].lower())}


def important_status(files: dict[str, FileInfo], expected: dict[str, dict[str, Any]] | None, result: dict[str, Any] | None) -> dict[str, Any]:
    wanted = ["PS3_DISC.SFB", "PS3_GAME/PARAM.SFO", "PS3_GAME/USRDIR/EBOOT.BIN"]
    wanted += sorted(info.path for info in files.values() if info.path.upper().endswith((".SELF", ".SPRX")))
    output: dict[str, Any] = {}
    renamed_by_expected = {normalize(row["expected"]): row for row in (result or {}).get("renamed", [])}
    modified_by_expected = {normalize(row["expected"]): row for row in (result or {}).get("modified", [])}
    for rel in dict.fromkeys(wanted):
        norm = normalize(rel)
        if norm in modified_by_expected:
            status = "modified"
            actual = modified_by_expected[norm]["actual"]
        elif norm in renamed_by_expected:
            status = "reconstructed filename only"
            actual = renamed_by_expected[norm]["actual"]
        elif norm in files and expected and norm in expected and files[norm].md5 == expected[norm]["md5"]:
            status = "retail/original"
            actual = files[norm].path
        elif norm in files:
            status = "unknown"
            actual = files[norm].path
        else:
            status = "missing"
            actual = None
        output[rel] = {"status": status, "actual": actual}
    output["LICDIR"] = {"status": "present" if any(n.startswith("ps3_game/licdir/") for n in files) else "absent"}
    output["TROPDIR"] = {"status": "present" if any(n.startswith("ps3_game/tropdir/") for n in files) else "absent"}
    return output


def sanitize_title(title: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", title).strip().rstrip(".")
    value = re.sub(r"\s+", " ", value)
    return value or "Unknown PS3 Game"


def audit_text(audit: dict[str, Any]) -> str:
    checks = audit.get("classification", {})
    lines = [
        f"PS3 INGEST {audit.get('result', 'UNKNOWN')}",
        f"timestamp: {audit['timestamp']}",
        f"source: {audit['source_path']}",
        f"title: {audit.get('title', '')}",
        f"title_id: {audit.get('title_id', '')}",
        f"source_files: {audit.get('source_file_count', 0)}",
        f"source_directories: {audit.get('source_directory_count', 0)}",
        f"source_bytes: {audit.get('source_bytes', 0)}",
        f"source_fingerprint: {audit.get('source_fingerprint', '')}",
        f"ird: {audit.get('ird_path') or 'NONE'}",
        f"ird_sha256: {audit.get('ird_sha256') or 'NONE'}",
        f"exact_matches: {len(checks.get('exact', []))}",
        f"renamed_hash_matches: {len(checks.get('renamed', []))}",
        f"missing: {len(checks.get('missing', []))}",
        f"required_missing: {len(audit.get('required_missing', []))}",
        f"optional_missing: {len(audit.get('optional_missing', []))}",
        f"extra: {len(checks.get('extra', []))}",
        f"modified: {len(checks.get('modified', []))}",
        f"ambiguous: {len(checks.get('ambiguous', []))}",
        f"executable_verification: {audit.get('executable_verification', 'unknown')}",
        f"param_sfo_status: {audit.get('param_sfo_status', 'unknown')}",
        f"build_tool: {audit.get('build_tool', TOOL_RELEASE)}",
        f"iso_path: {audit.get('iso_path') or 'NONE'}",
        f"iso_bytes: {audit.get('iso_bytes') or 0}",
        f"iso_sha256: {audit.get('iso_sha256') or 'NONE'}",
        f"final_validation: {audit.get('final_validation', 'NOT_RUN')}",
    ]
    for key in ("renamed", "missing", "extra", "modified", "ambiguous"):
        lines.append(f"\n[{key}]")
        for row in checks.get(key, []):
            lines.append(json.dumps(row, sort_keys=True))
    lines.append("\n[reconstruction_actions]")
    lines.extend(json.dumps(row, sort_keys=True) for row in audit.get("reconstruction_actions", []))
    return "\n".join(lines) + "\n"


def write_audit(audit: dict[str, Any]) -> tuple[Path, Path]:
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(AUDIT_ROOT, 0o750)
    base = f"{audit['timestamp']}-{audit.get('title_id') or 'UNKNOWN'}-{audit.get('source_fingerprint', 'none')[:12]}"
    json_path = AUDIT_ROOT / f"{base}.json"
    text_path = AUDIT_ROOT / f"{base}.txt"
    for path, content in ((json_path, json.dumps(audit, indent=2, sort_keys=True) + "\n"), (text_path, audit_text(audit))):
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o640)
        temporary.replace(path)
    return json_path, text_path


def find_prior(audit: dict[str, Any], final_path: Path) -> dict[str, Any] | None:
    if not final_path.is_file() or not AUDIT_ROOT.exists():
        return None
    for path in sorted(AUDIT_ROOT.glob("*.json"), reverse=True):
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if previous.get("source_fingerprint") == audit["source_fingerprint"] and previous.get("final_validation") == "PASS" and previous.get("iso_path") == str(final_path):
            if previous.get("iso_sha256") == hash_file(final_path, "sha256"):
                return previous
    return None


def known_iso_record(path: Path, source_fingerprint: str | None = None) -> dict[str, Any] | None:
    """Return a previously verified library ISO only when its recorded bytes still match."""
    if not path.is_file() or not KNOWN_ISOS.is_file():
        return None
    try:
        records = json.loads(KNOWN_ISOS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for record in records if isinstance(records, list) else []:
        if record.get("path") != str(path) or record.get("size") != path.stat().st_size or (source_fingerprint and record.get("source_fingerprint") != source_fingerprint):
            continue
        if record.get("sha256") == hash_file(path, "sha256"):
            return record
    return None


def known_iso_for_title(title_id: str, source_fingerprint: str | None = None) -> dict[str, Any] | None:
    if not KNOWN_ISOS.is_file():
        return None
    try:
        records = json.loads(KNOWN_ISOS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    matches = []
    for record in records if isinstance(records, list) else []:
        path = Path(str(record.get("path", "")))
        if record.get("title_id") != title_id or not path.is_file() or (source_fingerprint and record.get("source_fingerprint") != source_fingerprint):
            continue
        if record.get("size") == path.stat().st_size and record.get("sha256") == hash_file(path, "sha256"):
            matches.append(record)
    if len(matches) > 1:
        raise PreflightError(f"multiple verified ISOs recorded for {title_id}")
    return matches[0] if matches else None


def record_known_iso(path: Path, title_id: str, source_fingerprint: str, sha256: str) -> None:
    records: list[dict[str, Any]] = []
    if KNOWN_ISOS.is_file():
        try:
            value = json.loads(KNOWN_ISOS.read_text(encoding="utf-8"))
            records = value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            records = []
    records = [row for row in records if row.get("path") != str(path)]
    records.append({"path": str(path), "title_id": title_id, "source_fingerprint": source_fingerprint, "size": path.stat().st_size, "sha256": sha256, "verified_at": utcstamp(), "verification": "mounted filesystem plus IRD"})
    temporary = KNOWN_ISOS.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o640)
    temporary.replace(KNOWN_ISOS)


def refuse_existing_iso(final_path: Path) -> None:
    if final_path.exists():
        raise PreflightError(f"refusing to overwrite existing ISO: {final_path}")


def copy_work(source: Path, work: Path) -> str:
    work.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cp", "-a", "--reflink=always", f"{source}/.", str(work)], check=True, capture_output=True, text=True)
        return "reflink"
    except subprocess.CalledProcessError:
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        subprocess.run(["cp", "-a", "--reflink=auto", f"{source}/.", str(work)], check=True)
        return "full-copy (reflink unsupported)"


def reconstruct(work: Path, classification: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in classification["renamed"]:
        source = work / row["actual"]
        destination = work / row["expected"]
        if destination.exists():
            raise PreflightError(f"reconstruction collision: {row['expected']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        actions.append({"action": "rename", "from": row["actual"], "to": row["expected"], "proof": f"IRD MD5 {row['md5']}"})
    for row in classification["extra"]:
        path = work / row["actual"]
        path.unlink()
        actions.append({"action": "exclude", "path": row["actual"], "proof": "absent from trusted IRD filesystem"})
    expected_dirs = {"."}
    for row in classification["exact"] + classification["renamed"]:
        parent = Path(row["expected"]).parent
        while str(parent) not in ("", "."):
            expected_dirs.add(parent.as_posix().lower())
            parent = parent.parent
    for path in sorted((p for p in work.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(work).as_posix().lower()
        if rel not in expected_dirs:
            try:
                path.rmdir()
                actions.append({"action": "exclude-empty-directory", "path": path.relative_to(work).as_posix(), "proof": "absent from trusted IRD filesystem"})
            except OSError:
                pass
    return actions


def validate_iso(iso: Path, expected: dict[str, dict[str, Any]], title_id: str, work: Path) -> dict[str, Any]:
    mountpoint = work / ".iso-check"
    mountpoint.mkdir()
    mounted = False
    try:
        subprocess.run(["mount", "-o", "loop,ro", str(iso), str(mountpoint)], check=True, capture_output=True, text=True)
        mounted = True
        files, count, directories, total, _fingerprint = inventory(mountpoint)
        extra = sorted(info.path for norm, info in files.items() if norm not in expected)
        missing = sorted(entry["path"] for norm, entry in expected.items() if norm not in files and norm not in ALLOWED_ISO_OMISSIONS)
        mismatches = []
        for norm, info in files.items():
            target = expected.get(norm)
            if target and (info.md5 != target["md5"] or info.size != target["size"]):
                mismatches.append(info.path)
        for required in ("ps3_disc.sfb", "ps3_game/param.sfo", "ps3_game/usrdir/eboot.bin"):
            if required not in files:
                missing.append(expected.get(required, {"path": required})["path"])
        sfo = parse_sfo(mountpoint / files["ps3_game/param.sfo"].path) if "ps3_game/param.sfo" in files else {}
        mounted_id = canonical_title_id(str(sfo.get("TITLE_ID", "")))
        if mounted_id != title_id:
            mismatches.append(f"PARAM.SFO TITLE_ID {mounted_id or 'missing'} != {title_id}")
        return {"file_count": count, "directory_count": directories, "bytes": total, "missing": sorted(set(missing)), "extra": extra, "mismatches": mismatches, "title_id": mounted_id, "allowed_omissions": sorted(ALLOWED_ISO_OMISSIONS)}
    finally:
        os.chdir("/")
        if mounted:
            subprocess.run(["umount", str(mountpoint)], check=True)


def executable_verification(statuses: dict[str, Any]) -> str:
    executable = [value["status"] for key, value in statuses.items() if key.upper().endswith(("EBOOT.BIN", ".SELF", ".SPRX"))]
    proven = {"retail/original", "reconstructed filename only"}
    return "retail/original bytes" if executable and all(value in proven for value in executable) else "FAILED/UNVERIFIED"


def run(args: argparse.Namespace) -> int:
    timestamp = utcstamp()
    source_arg = Path(args.source)
    source = find_game_root(source_arg)
    sfo_path = source / "PS3_GAME" / "PARAM.SFO"
    metadata = parse_sfo(sfo_path)
    title_id = canonical_title_id(str(metadata.get("TITLE_ID", ""))) or canonical_title_id(str(source))
    title = str(metadata.get("TITLE", "")).strip()
    if not title_id or not title:
        raise PreflightError("cannot identify title and title ID with reasonable confidence")
    files, file_count, directory_count, source_bytes, source_fingerprint = inventory(source)
    audit: dict[str, Any] = {
        "schema": 1, "ps3_ingest_version": VERSION, "timestamp": timestamp, "mode": "check" if args.check else "build",
        "source_path": str(source), "title": title, "title_id": title_id, "param_sfo_metadata": metadata,
        "source_file_count": file_count, "source_directory_count": directory_count, "source_bytes": source_bytes,
        "source_fingerprint": source_fingerprint, "build_tool": TOOL_RELEASE, "build_tool_source": TOOL_SOURCE,
        "build_tool_sha256": TOOL_SHA256, "reconstruction_actions": [], "iso_path": None, "iso_bytes": None,
        "iso_sha256": None, "final_validation": "NOT_RUN",
    }
    ird_path = locate_ird(title_id, args.ird)
    if not ird_path:
        audit.update({"ird_path": None, "ird_sha256": None, "classification": {"exact": [], "renamed": [], "missing": [], "extra": [], "modified": [], "ambiguous": [], "unknown_unverified": [info.path for info in files.values()]}, "important_files": important_status(files, None, None), "executable_verification": "FAILED/UNVERIFIED", "param_sfo_status": "unknown", "result": "FAILED PREFLIGHT", "failure_reasons": ["no trusted matching IRD available"]})
        paths = write_audit(audit)
        print_summary(audit, paths)
        return 2
    ird, expected, ird_sha = load_ird(ird_path)
    if canonical_title_id(str(ird.product_code)) != title_id:
        raise PreflightError(f"IRD product {ird.product_code} contradicts source {title_id}")
    classification = classify(files, expected)
    statuses = important_status(files, expected, classification)
    exec_status = executable_verification(statuses)
    final_path = ISO_ROOT / f"{sanitize_title(title)} [{title_id}]-IRD-reconstructed.iso"
    known_iso = known_iso_record(final_path, source_fingerprint) or known_iso_for_title(title_id, source_fingerprint)
    if known_iso:
        final_path = Path(known_iso["path"])
    failure_reasons = []
    required_missing = [row for row in classification["missing"] if normalize(row["expected"]) not in ALLOWED_SOURCE_OMISSIONS]
    optional_missing = [row for row in classification["missing"] if normalize(row["expected"]) in ALLOWED_SOURCE_OMISSIONS]
    if required_missing:
        failure_reasons.append(f"{len(required_missing)} required retail files missing")
    if classification["modified"]:
        failure_reasons.append(f"{len(classification['modified'])} exact-path files have hash/size mismatches")
    if classification["ambiguous"]:
        failure_reasons.append(f"{len(classification['ambiguous'])} ambiguous hash mappings")
    if exec_status != "retail/original bytes":
        failure_reasons.append("EBOOT/SELF/SPRX verification unresolved or mismatched")
    audit.update({"ird_path": str(ird_path), "ird_sha256": ird_sha, "ird_product_code": ird.product_code, "ird_title": ird.title, "ird_file_count": len(expected), "classification": classification, "required_missing": required_missing, "optional_missing": optional_missing, "important_files": statuses, "executable_verification": exec_status, "param_sfo_status": statuses["PS3_GAME/PARAM.SFO"]["status"], "failure_reasons": failure_reasons, "existing_iso": {"path": str(final_path), "status": "preserved and not rebuilt", "sha256": known_iso.get("sha256")} if known_iso else None})
    audit["result"] = "FAILED PREFLIGHT" if failure_reasons else "PREFLIGHT PASS"
    if args.check or failure_reasons:
        paths = write_audit(audit)
        print_summary(audit, paths)
        return 2 if failure_reasons else 0
    if not MAKEPS3ISO.is_file() or hash_file(MAKEPS3ISO, "sha256") != TOOL_SHA256:
        raise PreflightError("pinned makeps3iso binary missing or checksum mismatch")
    if known_iso:
        audit.update({"result": "ALREADY VALIDATED", "iso_path": str(final_path), "iso_bytes": final_path.stat().st_size, "iso_sha256": known_iso["sha256"], "final_validation": "PASS", "rerun_of": known_iso.get("verified_at")})
        paths = write_audit(audit)
        print_summary(audit, paths)
        return 0
    prior = find_prior(audit, final_path)
    if prior:
        audit.update({"result": "ALREADY VALIDATED", "iso_path": str(final_path), "iso_bytes": final_path.stat().st_size, "iso_sha256": prior["iso_sha256"], "final_validation": "PASS", "rerun_of": prior["timestamp"]})
        paths = write_audit(audit)
        print_summary(audit, paths)
        return 0
    try:
        refuse_existing_iso(final_path)
    except PreflightError as exc:
        audit.update({"result": "FAILED PREFLIGHT", "failure_reasons": [str(exc)]})
        paths = write_audit(audit)
        print_summary(audit, paths)
        return 2
    unique = f"{title_id}-{timestamp}-{source_fingerprint[:8]}"
    work = WORK_ROOT / unique
    temp_iso = ISO_ROOT / f".building-{unique}.iso"
    if work.exists() or temp_iso.exists():
        raise PreflightError("unique work/build path already exists")
    audit["work_path"] = str(work)
    try:
        copy_mode = copy_work(source, work)
        audit["copy_mode"] = copy_mode
        audit["reconstruction_actions"] = reconstruct(work, classification)
        work_files, *_ = inventory(work)
        work_result = classify(work_files, expected)
        work_required_missing = [row for row in work_result["missing"] if normalize(row["expected"]) not in ALLOWED_SOURCE_OMISSIONS]
        if work_required_missing or work_result["modified"] or work_result["renamed"] or work_result["ambiguous"] or work_result["extra"]:
            raise PreflightError("reconstructed work tree did not exactly match IRD")
        build_log = work / "makeps3iso.log"
        with build_log.open("wb") as output:
            completed = subprocess.run([str(MAKEPS3ISO), str(work), str(temp_iso)], stdout=output, stderr=subprocess.STDOUT)
        if completed.returncode != 0 or not temp_iso.is_file() or temp_iso.stat().st_size < 1024 * 1024:
            raise PreflightError(f"ISO build failed; retained work at {work}")
        iso_validation = validate_iso(temp_iso, expected, title_id, work)
        audit["post_build_validation"] = iso_validation
        if iso_validation["missing"] or iso_validation["extra"] or iso_validation["mismatches"]:
            raise PreflightError(f"ISO validation failed; retained work and temporary ISO: {iso_validation}")
        current_files, _, _, _, current_fingerprint = inventory(source)
        if current_fingerprint != source_fingerprint or set(current_files) != set(files):
            raise PreflightError("source changed during ingest; refusing finalization")
        iso_sha = hash_file(temp_iso, "sha256")
        os.chmod(temp_iso, 0o644)
        temp_iso.replace(final_path)
        audit.update({"result": "PASS", "iso_path": str(final_path), "iso_bytes": final_path.stat().st_size, "iso_sha256": iso_sha, "final_validation": "PASS", "source_unchanged": True})
        record_known_iso(final_path, title_id, source_fingerprint, iso_sha)
        paths = write_audit(audit)
        shutil.rmtree(work)
        print_summary(audit, paths)
        return 0
    except Exception as exc:
        audit.update({"result": "FAILED", "final_validation": "FAIL", "failure_reasons": audit.get("failure_reasons", []) + [str(exc)]})
        paths = write_audit(audit)
        print_summary(audit, paths)
        return 2


def print_summary(audit: dict[str, Any], paths: tuple[Path, Path]) -> None:
    c = audit.get("classification", {})
    print(f"{audit['result']}: {audit.get('title')} [{audit.get('title_id')}]")
    print(f"source={audit.get('source_path')} files={audit.get('source_file_count')} dirs={audit.get('source_directory_count')} bytes={audit.get('source_bytes')}")
    print(f"ird={audit.get('ird_path') or 'NONE'} exact={len(c.get('exact', []))} renamed={len(c.get('renamed', []))} missing_required={len(audit.get('required_missing', []))} missing_optional={len(audit.get('optional_missing', []))} extra={len(c.get('extra', []))} modified={len(c.get('modified', []))} ambiguous={len(c.get('ambiguous', []))}")
    print(f"executables={audit.get('executable_verification')} param_sfo={audit.get('param_sfo_status')}")
    for reason in audit.get("failure_reasons", []):
        print(f"reason={reason}")
    if audit.get("iso_path"):
        print(f"iso={audit['iso_path']} sha256={audit.get('iso_sha256')}")
    if audit.get("existing_iso"):
        print(f"existing_iso={audit['existing_iso']['path']} status={audit['existing_iso']['status']}")
    print(f"audit_json={paths[0]}")
    print(f"audit_text={paths[1]}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ps3-ingest", description=__doc__)
    parser.add_argument("--check", action="store_true", help="inspect only; never create a work tree or ISO")
    parser.add_argument("--ird", help="explicit trusted IRD path; otherwise use the pinned local cache")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("source", help="untrusted PS3 folder or parent containing exactly one PS3_GAME")
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("ps3-ingest must run as root (use sudo)", file=sys.stderr)
        return 2
    for directory in (STATE_ROOT, IRD_ROOT, AUDIT_ROOT, WORK_ROOT, ISO_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock:
        lock_mode = fcntl.LOCK_SH if args.check else fcntl.LOCK_EX
        fcntl.flock(lock, lock_mode | fcntl.LOCK_NB)
        try:
            return run(args)
        except (PreflightError, OSError, subprocess.SubprocessError) as exc:
            print(f"FAILED PREFLIGHT: {exc}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
