"""Versioned paper archive. Writers and readers hold archive_lock during recovery and use."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

PREFIXES = tuple(f"{i:02x}" for i in range(256))
PAPER_ID = re.compile(r"[0-9a-f]{16}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def formatted(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


@contextmanager
def archive_lock(data: Path):
    # The OS releases this lock if a collector is terminated mid-checkpoint.
    with (data / ".archive-lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if not lock.tell():
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class Archive:
    def __init__(self, data: Path):
        self.data = data
        self.papers = data / "papers"
        self.manifest_path = data / "manifest.json"
        self.prepare = data / ".archive-prepare"
        self.pending = data / ".archive-pending"
        self.manifest = None

    def _install(self):
        # A missing staged file was already renamed into place. The manifest moves last.
        self.papers.mkdir(exist_ok=True)
        for staged in sorted(self.pending.glob("*.jsonl")):
            if staged.stem not in PREFIXES:
                raise ValueError(f"Unexpected staged shard: {staged.name}")
            staged.replace(self.papers / staged.name)
        for name in ("state.json", "manifest.json"):
            staged = self.pending / name
            if staged.exists():
                staged.replace(self.data / name)
        self.pending.rmdir()

    def read(self):
        if self.pending.exists():
            self._install()
        if self.prepare.exists():
            shutil.rmtree(self.prepare)
        state_bytes = (self.data / "state.json").read_bytes()
        state = json.loads(state_bytes)
        if not self.manifest_path.exists():
            if self.papers.exists():
                raise ValueError("Missing manifest for sharded archive")
            payload = json.loads((self.data / "store.json").read_bytes())
            if payload.get("schema_version", 1) != 1 or set(payload) - {"papers", "schema_version"}:
                raise ValueError("Unsupported legacy archive")
            self._validate_ids(payload["papers"])
            return payload, state

        manifest = json.loads(self.manifest_path.read_bytes())
        if manifest["schema_version"] != 2 or manifest["prefix_length"] != 2:
            raise ValueError("Unsupported sharded archive version")
        if digest(state_bytes) != manifest["state_sha256"]:
            raise ValueError("State checksum mismatch")
        expected = set(PREFIXES)
        if set(manifest["shards"]) != expected or {p.name for p in self.papers.iterdir()} != {
            prefix + ".jsonl" for prefix in PREFIXES
        }:
            raise ValueError("Incomplete or unexpected shard set")
        papers = []
        for prefix in PREFIXES:
            raw = (self.papers / f"{prefix}.jsonl").read_bytes()
            meta = manifest["shards"][prefix]
            if digest(raw) != meta["sha256"]:
                raise ValueError(f"Shard {prefix}: checksum mismatch")
            rows = [json.loads(line) for line in raw.splitlines()]
            ids = [p["id"] for p in rows]
            if len(rows) != meta["count"] or ids != sorted(ids) or any(not i.startswith(prefix) for i in ids):
                raise ValueError(f"Shard {prefix}: count, order or routing mismatch")
            papers.extend(rows)
        if len(papers) != manifest["paper_count"]:
            raise ValueError("Archive count mismatch")
        self._validate_ids(papers)
        self.manifest = manifest
        return {"schema_version": 1, "papers": papers}, state

    @staticmethod
    def _validate_ids(papers):
        seen_ids = set()
        alias_owner = {}
        for paper in papers:
            pid = paper["id"]
            if not PAPER_ID.fullmatch(pid) or pid in seen_ids:
                raise ValueError(f"Invalid or repeated paper ID: {pid}")
            seen_ids.add(pid)
            for alias in paper["aliases"]:
                if alias_owner.setdefault(alias, pid) != pid:
                    raise ValueError(f"Alias belongs to two papers: {alias}")

    def checkpoint(self, store, state, *, full=False, state_bytes=None):
        old = self.manifest or {}
        prefixes = set(PREFIXES) if full or not old else store.dirty_prefixes
        grouped = defaultdict(list)
        for pid, paper in store.papers.items():
            if pid[:2] in prefixes:
                grouped[pid[:2]].append(paper)
        shards = dict(old.get("shards", {}))
        self.prepare.mkdir()
        for prefix in sorted(prefixes):
            rows = sorted(grouped[prefix], key=lambda p: p["id"])
            raw = b"".join(json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode() + b"\n" for p in rows)
            meta = {"count": len(rows), "sha256": digest(raw)}
            if shards.get(prefix) != meta:
                (self.prepare / f"{prefix}.jsonl").write_bytes(raw)
                shards[prefix] = meta
        if state_bytes is None:
            state_bytes = formatted(state)
        state_hash = digest(state_bytes)
        manifest = {"schema_version": 2, "prefix_length": 2, "paper_count": len(store.papers),
                    "state_sha256": state_hash, "shards": shards}
        if sum(shard["count"] for shard in shards.values()) != len(store.papers):
            raise ValueError("Changed paper missed by shard tracking")
        if manifest == old:
            self.prepare.rmdir()
        else:
            if state_hash != old.get("state_sha256"):
                (self.prepare / "state.json").write_bytes(state_bytes)
            (self.prepare / "manifest.json").write_bytes(formatted(manifest))
            self.prepare.replace(self.pending)
            self._install()
            self.manifest = manifest
        store.dirty_prefixes.clear()
