import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from radar.archive import Archive, archive_lock, formatted
from radar.core import Store, make_record

CFG = {"site": {"minimum_score": 0}, "topics": []}


def record(key, **details):
    return make_record(source="source-" + key, family="test", title="Paper " + key,
                       url="https://example.org/" + key, **details)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.state = {"sources": {"source": {"last_success_at": "previous"}},
                      "detail_cache": {"url": {"custom": 123}}, "last_run": {}}
        self.state_bytes = json.dumps(self.state).encode()
        (self.data / "state.json").write_bytes(self.state_bytes)
        seed = Store({"papers": []}, CFG)
        seed.ingest([record("a", doi="10.1000/journal"), record("b", arxiv_id="2501.00001")], "2026-10-01")
        self.seed = seed.payload()
        (self.data / "store.json").write_bytes(formatted(self.seed))
        self.archive = Archive(self.data)

    def migrate(self):
        payload, state = self.archive.read()
        store = Store(payload, CFG)
        self.archive.checkpoint(store, state, full=True, state_bytes=self.state_bytes)
        return store

    def test_exact_roundtrip_and_frozen_legacy(self):
        with archive_lock(self.data):
            old, _ = self.archive.read()
            store = self.migrate()
            restored, state = Archive(self.data).read()
            self.assertEqual(formatted(restored), (self.data / "store.json").read_bytes())
            self.assertEqual(state, self.state)
            self.assertEqual((self.data / "state.json").read_bytes(), self.state_bytes)
            self.assertEqual(store.index, Store(old, CFG).index)
            self.assertEqual(len(list(self.archive.papers.glob("*.jsonl"))), 256)

    def test_merge_updates_both_shards_and_replay_changes_no_shards(self):
        store = self.migrate()
        ids = list(store.papers)
        self.assertNotEqual(ids[0][:2], ids[1][:2])
        original = self.archive.manifest["shards"]
        bridge = record("c", doi="10.1000/journal", arxiv_id="2501.00001")
        store.ingest([bridge], "2026-11-18")
        self.archive.checkpoint(store, self.state)
        merged = self.archive.manifest["shards"]
        self.assertEqual({i for i in merged if merged[i] != original[i]}, {p[:2] for p in ids})
        restored, state = self.archive.read()
        self.assertEqual(Store(restored, CFG).index, store.index)
        self.assertEqual(len(restored["papers"]), 1)
        self.assertEqual(len(restored["papers"][0]["records"]), 3)
        mtimes = {p: p.stat().st_mtime_ns for p in self.archive.papers.iterdir()}
        store.ingest([bridge], "2026-11-19")
        self.archive.checkpoint(store, {**state, "last_run": {"changed": 0}})
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in mtimes})

    def test_final_pass_persists_overrides_outside_last_source(self):
        store = self.migrate()
        pid = next(iter(store.papers))
        store.apply_overrides([{"match": pid, "document_type": "review"}])
        self.archive.checkpoint(store, self.state, full=True)
        restored, _ = self.archive.read()
        self.assertEqual(Store(restored, CFG).papers[pid]["document_type"], "review")

    def test_fail_closed_when_either_shard_state_or_manifest_is_missing_or_corrupt(self):
        self.migrate()
        for path in (self.archive.papers / "00.jsonl", self.data / "state.json", self.archive.manifest_path):
            original = path.read_bytes()
            for damaged in (None, b"{}"):
                with self.subTest(path=path.name, damaged=damaged):
                    if damaged is None:
                        path.unlink()
                    else:
                        path.write_bytes(damaged)
                    with self.assertRaises((OSError, ValueError, KeyError)):
                        Archive(self.data).read()
                    path.write_bytes(original)

    def test_recover_each_interrupted_install_boundary(self):
        store = self.migrate()
        original_replace = Path.replace
        # Fail before the journal becomes visible, then at each live-file replacement.
        for phase in ("prepare", "shard", "state", "manifest"):
            with self.subTest(phase=phase):
                archive = Archive(self.data)
                old, old_state = archive.read()
                store = Store(old, CFG)
                store.ingest([record(phase)], "2026-11-20")
                new_state = {**old_state, "phase": phase}
                def interrupt(src, dest):
                    is_shard = src.parent == archive.pending and src.suffix == ".jsonl"
                    target = {"prepare": src == archive.prepare, "shard": is_shard,
                              "state": src == archive.pending / "state.json",
                              "manifest": src == archive.pending / "manifest.json"}[phase]
                    if target:
                        raise InterruptedError(phase)
                    return original_replace(src, dest)
                with patch.object(Path, "replace", interrupt), self.assertRaises(InterruptedError):
                    archive.checkpoint(store, new_state)
                expected = (old, old_state) if phase == "prepare" else (store.payload(), new_state)
                self.assertEqual(Archive(self.data).read(), expected)
                self.assertEqual(Archive(self.data).read(), expected)

    def test_duplicate_legacy_id_or_alias_rejected_before_conversion(self):
        for collision in ("id", "aliases"):
            rows = json.loads(json.dumps(self.seed["papers"]))
            rows[1][collision] = rows[0][collision]
            (self.data / "store.json").write_bytes(formatted({"papers": rows}))
            with self.assertRaises(ValueError):
                self.archive.read()
