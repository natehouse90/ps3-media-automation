#!/usr/bin/env python3
"""Safely import PS3/PSP/PS2/PS1/homebrew content from SAB's ps3 category.

Sources are never deleted. Imports are copied to a temporary destination,
verified, and atomically renamed into the read-only ps3netsrv library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


LIBRARY = Path(os.environ.get("PS3_LIBRARY_ROOT", "/srv/ps3-library"))
INCOMING = Path(os.environ.get("PS3_INCOMING_DIR", str(LIBRARY / "incoming")))
LOG_PATH = LIBRARY / ".ps3-import.log"
RELAY_URL = os.environ.get("PS3_REFRESH_RELAY_URL", "")
RELAY_TOKEN_PATH = Path(os.environ.get("PS3_REFRESH_RELAY_TOKEN_FILE", "/etc/ps3-refresh-relay.token"))
PENDING_PATH = LIBRARY / ".ps3-refresh-pending.json"
PS3_INGEST = Path(os.environ.get("PS3_INGEST_COMMAND", "/usr/local/sbin/ps3-ingest"))

DESTINATIONS = {
    "PS3ISO": LIBRARY / "PS3ISO",
    "GAMES": LIBRARY / "GAMES",
    "PSXISO": LIBRARY / "PSXISO",
    "PS2ISO": LIBRARY / "PS2ISO",
    "PSPISO": LIBRARY / "PSPISO",
    "PKG": LIBRARY / "PKG",
}

PLATFORM_ID = re.compile(r"(?<![A-Z0-9])[A-Z]{4}\d{5}(?![A-Z0-9])", re.IGNORECASE)
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
INCOMPLETE_SUFFIXES = (".part", ".partial", ".tmp", ".nzb", ".!qb", ".incomplete")

class XmbRefreshPending(RuntimeError):
    """The PS3 is not safely at XMB, so the relay deferred the reload."""


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("ps3-import")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(stream_handler)
    return logger


def title_id(path: Path) -> str:
    match = PLATFORM_ID.search(" ".join(path.parts))
    return match.group(0).upper() if match else "unknown"


def digest(path: Path) -> tuple[int, str]:
    total = 0
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            sha.update(chunk)
    return total, sha.hexdigest()


def tree_manifest(root: Path) -> list[tuple[str, int, str]]:
    result = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        size, checksum = digest(path)
        result.append((str(path.relative_to(root)), size, checksum))
    return result


def safe_extract_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise RuntimeError(f"unsafe archive path: {member.filename}")
        archive.extractall(destination)


def safe_extract_tar(source: Path, destination: Path) -> None:
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise RuntimeError(f"unsafe archive path: {member.name}")
        archive.extractall(destination)


def extract_archive(source: Path, destination: Path) -> None:
    lower = source.name.lower()
    if lower.endswith(".zip"):
        safe_extract_zip(source, destination)
    elif lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        safe_extract_tar(source, destination)
    elif lower.endswith((".7z", ".rar")):
        tool = shutil.which("7z") or shutil.which("7zz")
        if not tool:
            raise RuntimeError("7z/7zz is not installed for this archive type")
        subprocess.run([tool, "x", "-y", f"-o{destination}", str(source)], check=True, capture_output=True)
    else:
        raise RuntimeError("unsupported archive type")


def iso_listing(path: Path) -> str:
    tool = shutil.which("7z") or shutil.which("7zz")
    if not tool:
        return ""
    try:
        result = subprocess.run([tool, "l", "-ba", str(path)], check=False, text=True, capture_output=True, timeout=30)
        return result.stdout.upper()
    except (OSError, subprocess.SubprocessError):
        return ""


def classify_file(path: Path, context: str = "") -> str | None:
    lower = path.name.lower()
    if any(lower.endswith(suffix) for suffix in INCOMPLETE_SUFFIXES):
        return None
    if path.stat().st_size <= 0:
        return None
    if lower.endswith(".pkg"):
        if path.stat().st_size < 256:
            return None
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic not in (b"\x7fPKG", b"PKG\x00"):
            return None
        return "PKG"
    if lower.endswith(".cue"):
        return "PSXISO"
    if lower.endswith(".bin") and re.search(r"(psx|ps1|playstation)", lower):
        return "PSXISO"
    if not lower.endswith(".iso"):
        return None
    listing = iso_listing(path)
    haystack = f"{path} {context}".upper()
    if "PS3_GAME" in listing or re.search(r"\bPS3\b", haystack):
        return "PS3ISO"
    if "PSP_GAME" in listing or re.search(r"\bPSP\b", haystack):
        return "PSPISO"
    if re.search(r"\bPS2\b", haystack):
        return "PS2ISO"
    if re.search(r"\bPSX\b|\bPS1\b|PLAYSTATION", haystack):
        return "PSXISO"
    return None


def find_ps3_folder(root: Path) -> Path | None:
    for candidate in root.rglob("PS3_GAME"):
        if candidate.is_dir():
            return candidate.parent
    return None


def hardened_ps3_ingest(folder: Path, check_only: bool, logger: logging.Logger) -> bool:
    """Route PS3 folders through IRD-backed, source-immutable validation/building."""
    command = [str(PS3_INGEST)]
    if check_only:
        command.append("--check")
    command.append(str(folder))
    completed = subprocess.run(command, text=True, capture_output=True, timeout=None)
    detail = " ".join(line.strip() for line in completed.stdout.splitlines()[:4] if line.strip())
    if completed.returncode != 0:
        logger.error("PS3 folder failed hardened ingest source=%s result=%s", folder, detail or "see the configured PS3 audit directory")
        return False
    logger.info("PS3 folder passed hardened ingest source=%s result=%s", folder, detail)
    return True


def safe_file_copy(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".importing-{destination.name}-{os.getpid()}"
    try:
        shutil.copy2(source, temporary)
        if digest(source) != digest(temporary):
            raise RuntimeError(f"verification failed for {source}")
        temporary.replace(destination)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def safe_tree_copy(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".importing-{destination.name}-{os.getpid()}"
    try:
        shutil.copytree(source, temporary)
        if tree_manifest(source) != tree_manifest(temporary):
            raise RuntimeError(f"verification failed for {source}")
        temporary.replace(destination)
        return True
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def cue_sources(cue: Path) -> list[Path]:
    sources = [cue]
    text = cue.read_text(errors="replace")
    for match in re.finditer(r'^FILE\s+"([^"]+)"', text, re.IGNORECASE | re.MULTILINE):
        candidate = cue.parent / match.group(1)
        if candidate.is_file():
            sources.append(candidate)
    return sources


def refresh_net0(logger: logging.Logger) -> None:
    if not RELAY_URL:
        raise RuntimeError("PS3_REFRESH_RELAY_URL is not configured")
    token = RELAY_TOKEN_PATH.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("refresh relay token is missing or invalid")
    request = urllib.request.Request(
        RELAY_URL,
        data=b"",
        method="POST",
        headers={"Content-Length": "0", "X-PS3-Refresh-Token": token},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
    try:
        relay_result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("refresh relay returned invalid JSON") from exc
    if relay_result.get("action") == "xmb-refresh-pending":
        raise XmbRefreshPending(relay_result.get("reason", "PS3 is not idle"))
    if response.status != 200:
        raise RuntimeError(f"refresh relay returned HTTP {response.status}")
    if relay_result.get("ok") is not True or relay_result.get("action") != "net-refresh-xmb":
        raise RuntimeError("refresh relay did not confirm net-refresh-xmb")
    logger.info("webMAN scan and XMB refresh complete via Windows relay; ps3ctl confirmed net-refresh-xmb")


def load_pending() -> list[str]:
    if not PENDING_PATH.exists():
        return []
    try:
        value = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        return [str(item) for item in value if isinstance(item, str)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def save_pending(items: list[str]) -> None:
    if not items:
        PENDING_PATH.unlink(missing_ok=True)
        return
    temporary = PENDING_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(sorted(set(items)), indent=2) + "\n", encoding="utf-8")
    temporary.replace(PENDING_PATH)


def mark_pending(source: Path) -> None:
    items = load_pending()
    items.append(str(source))
    save_pending(items)


def clear_pending(source: Path) -> None:
    save_pending([item for item in load_pending() if item != str(source)])


def retry_pending(logger: logging.Logger, dry_run: bool) -> None:
    if dry_run:
        return
    pending = load_pending()
    if not pending:
        return
    try:
        refresh_net0(logger)
    except XmbRefreshPending as exc:
        logger.warning("XMB refresh pending; PS3 is not idle: %s", exc)
    except Exception:
        logger.warning("webMAN refresh pending; Windows relay unavailable or refresh was rejected")
    else:
        save_pending([])
        logger.info("webMAN refresh pending queue resolved count=%d", len(pending))


def import_one(source: Path, logger: logging.Logger, dry_run: bool) -> bool:
    if source.name.startswith("."):
        return False
    work = None
    try:
        root = source
        if source.is_file() and source.name.lower().endswith(ARCHIVE_SUFFIXES):
            work = Path(tempfile.mkdtemp(prefix=".ps3-import-", dir=INCOMING))
            extract_archive(source, work)
            root = work

        folder = find_ps3_folder(root) if root.is_dir() else None
        if folder:
            logger.info("candidate type=PS3_FOLDER title_id=%s source=%s", title_id(folder), source)
            imported = hardened_ps3_ingest(folder, dry_run, logger)
        else:
            candidates = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
            imported = False
            for candidate in candidates:
                kind = classify_file(candidate, str(source))
                if not kind:
                    continue
                destination_dir = DESTINATIONS[kind]
                logger.info("candidate type=%s title_id=%s source=%s", kind, title_id(candidate), source)
                if dry_run:
                    imported = True
                    continue
                if kind == "PSXISO" and candidate.suffix.lower() == ".cue":
                    copied = True
                    for member in cue_sources(candidate):
                        copied = safe_file_copy(member, destination_dir / member.name) or copied
                    imported = imported or copied
                else:
                    imported = safe_file_copy(candidate, destination_dir / candidate.name) or imported
        if imported and not dry_run:
            logger.info("import verified source=%s source_preserved=true", source)
            try:
                refresh_net0(logger)
            except XmbRefreshPending as exc:
                mark_pending(source)
                logger.warning("XMB refresh pending after verified import source=%s reason=%s", source, exc)
            except Exception as exc:
                mark_pending(source)
                logger.warning("webMAN refresh pending after verified import source=%s", source)
            else:
                clear_pending(source)
        elif not imported:
            logger.warning("no recognized complete content source=%s", source)
        return imported
    except Exception as exc:
        logger.error("import failed source=%s error=%s", source, exc)
        return False
    finally:
        if work and work.exists():
            shutil.rmtree(work)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source", help="process one incoming file or directory")
    args = parser.parse_args()
    INCOMING.mkdir(parents=True, exist_ok=True)
    logger = setup_logging()
    retry_pending(logger, args.dry_run)
    sources = [Path(args.source)] if args.source else sorted(p for p in INCOMING.iterdir() if not p.name.startswith(".") and p.name != ".ps3-import-work")
    imported = 0
    for source in sources:
        imported += int(import_one(source, logger, args.dry_run))
    logger.info("run complete candidates=%d imported=%d dry_run=%s", len(sources), imported, args.dry_run)
    print(f"candidates={len(sources)} imported={imported} dry_run={str(args.dry_run).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
