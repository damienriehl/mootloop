"""Small durability and exact-byte hashing primitives shared by vault stores."""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_fsync_line(path: Path, line: str) -> None:
    """Repair a torn tail, append one locked UTF-8 line, and flush durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0, os.SEEK_END)
        _repair_torn_tail(handle)
        handle.seek(0, os.SEEK_END)
        handle.write((line + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _repair_torn_tail(handle: BinaryIO) -> bool:
    handle.seek(0, os.SEEK_END)
    end = handle.tell()
    if not end:
        return False
    handle.seek(-1, os.SEEK_END)
    if handle.read(1) == b"\n":
        return False
    position = end
    truncate_at = 0
    while position:
        chunk_size = min(position, 64 * 1024)
        position -= chunk_size
        handle.seek(position)
        chunk = handle.read(chunk_size)
        newline = chunk.rfind(b"\n")
        if newline >= 0:
            truncate_at = position + newline + 1
            break
    handle.truncate(truncate_at)
    return True


def repair_torn_jsonl(path: Path) -> None:
    """Durably discard only a newline-less terminal fragment under an exclusive lock."""
    if not path.is_file():
        return
    with path.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if _repair_torn_tail(handle):
            handle.flush()
            os.fsync(handle.fileno())


def complete_jsonl_lines(path: Path) -> Iterator[str]:
    """Yield complete UTF-8 JSONL records, ignoring only a torn terminal fragment."""
    if not path.is_file():
        return
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            line = raw.decode("utf-8")
            if line.strip():
                yield line
