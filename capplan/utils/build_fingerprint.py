from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


def _file_stat(path: Path) -> Dict[str, Any]:
    try:
        st = path.stat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path.resolve()),
        "exists": True,
        "type": "file",
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
    }


def path_signature(path: str | os.PathLike[str] | None) -> Dict[str, Any] | None:
    """Return a cheap, deterministic input identity for resumable builds.

    This deliberately uses metadata rather than hashing multi-GB GIS/nuPlan
    inputs.  File size + nanosecond mtime detects normal download/rebuild/audit
    updates at negligible cost.  Directory signatures recursively list files so
    overwriting a file inside a stable directory also invalidates the cache.
    """
    if path is None or str(path).strip() == "":
        return None
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False}
    if p.is_file():
        return _file_stat(p)
    entries = []
    for child in sorted((x for x in p.rglob("*") if x.is_file()), key=lambda x: str(x.relative_to(p))):
        try:
            st = child.stat()
        except FileNotFoundError:
            continue
        entries.append({
            "rel": str(child.relative_to(p)),
            "size": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns),
        })
    try:
        st = p.stat()
        mtime_ns = int(st.st_mtime_ns)
    except FileNotFoundError:
        mtime_ns = 0
    return {
        "path": str(p.resolve()),
        "exists": True,
        "type": "directory",
        "mtime_ns": mtime_ns,
        "files": entries,
    }


def fingerprint(payload: Mapping[str, Any], paths: Iterable[str | os.PathLike[str] | None] = ()) -> str:
    obj = {
        "payload": dict(payload),
        "inputs": [sig for sig in (path_signature(p) for p in paths) if sig is not None],
    }
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fingerprint_object(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_inventory_fingerprint(paths: Iterable[str | os.PathLike[str]]) -> str:
    """Fingerprint a concrete file inventory using only path/stat metadata.

    This is intentionally cheap enough for nuPlan split inventories containing
    thousands of SQLite files.  It detects added/removed/replaced DBs without
    opening the databases, allowing a previously audited city->DB mapping to be
    reused safely during per-city extraction.
    """
    entries = []
    for raw in sorted((Path(x) for x in paths), key=lambda x: str(x)):
        try:
            st = raw.stat()
            entries.append({
                "path": str(raw.resolve()),
                "exists": True,
                "size": int(st.st_size),
                "mtime_ns": int(st.st_mtime_ns),
            })
        except FileNotFoundError:
            entries.append({"path": str(raw), "exists": False})
    return fingerprint_object({"files": entries})
