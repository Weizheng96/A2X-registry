"""Tests for :mod:`src.client.ownership` — persistence + concurrency."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from threading import Thread

import pytest

from src.client.ownership import OwnershipStore


class TestBasicOperations:
    def test_memory_only_mode(self):
        s = OwnershipStore(file_path=None, base_url="http://x")
        s.add("ds", "sid")
        assert s.contains("ds", "sid")
        s.remove("ds", "sid")
        assert not s.contains("ds", "sid")

    def test_persistence_roundtrip(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "a")
        s.add("ds", "b")

        reloaded = OwnershipStore(ownership_path, "http://h")
        assert reloaded.contains("ds", "a")
        assert reloaded.contains("ds", "b")

    def test_remove_cleans_empty_bucket(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "only")
        s.add("ds2", "other")  # keep a second dataset so the base_url segment stays
        s.remove("ds", "only")
        data = json.loads(ownership_path.read_text("utf-8"))
        assert "ds" not in data["data"]["http://h"]
        assert "ds2" in data["data"]["http://h"]

    def test_remove_last_dataset_drops_base_url_segment(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "only")
        s.remove("ds", "only")
        data = json.loads(ownership_path.read_text("utf-8"))
        # When the base_url has no remaining datasets, its entire segment is removed.
        assert "http://h" not in data["data"]

    def test_remove_dataset_no_op_when_absent(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.remove_dataset("missing")  # should not raise, should not create file
        assert not ownership_path.exists()


class TestSchemaAndMigration:
    def test_writes_v1_wrapper(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "sid")
        doc = json.loads(ownership_path.read_text("utf-8"))
        assert doc["schema_version"] == 1
        assert doc["data"]["http://h"]["ds"] == ["sid"]

    def test_reads_legacy_flat(self, ownership_path: Path):
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text(
            json.dumps({"http://old": {"d": ["s1", "s2"]}}),
            encoding="utf-8",
        )
        s = OwnershipStore(ownership_path, "http://old")
        assert s.contains("d", "s1")
        assert s.contains("d", "s2")

    def test_legacy_migrated_on_first_write(self, ownership_path: Path):
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text(
            json.dumps({"http://a": {"d": ["s1"]}, "http://b": {"x": ["y"]}}),
            encoding="utf-8",
        )
        s = OwnershipStore(ownership_path, "http://a")
        s.add("d", "s2")
        doc = json.loads(ownership_path.read_text("utf-8"))
        assert doc["schema_version"] == 1
        assert set(doc["data"]["http://a"]["d"]) == {"s1", "s2"}
        # Other base_url segment preserved through migration
        assert doc["data"]["http://b"]["x"] == ["y"]

    def test_corrupt_file_tolerated(self, ownership_path: Path):
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text("{not valid json")
        s = OwnershipStore(ownership_path, "http://h")
        assert not s.contains("ds", "sid")  # starts clean
        s.add("ds", "sid")  # should still work
        doc = json.loads(ownership_path.read_text("utf-8"))
        assert doc["schema_version"] == 1


class TestMultipleBackends:
    def test_segments_isolated(self, ownership_path: Path):
        s_a = OwnershipStore(ownership_path, "http://A")
        s_b = OwnershipStore(ownership_path, "http://B")
        s_a.add("ds", "from_a")
        s_b.add("ds", "from_b")

        reload_a = OwnershipStore(ownership_path, "http://A")
        reload_b = OwnershipStore(ownership_path, "http://B")
        assert reload_a.contains("ds", "from_a")
        assert not reload_a.contains("ds", "from_b")
        assert reload_b.contains("ds", "from_b")
        assert not reload_b.contains("ds", "from_a")


class TestLockFile:
    def test_lock_file_created_alongside(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "sid")
        assert (ownership_path.parent / "owned.json.lock").exists()


class TestThreadSafety:
    def test_concurrent_adds_do_not_lose_updates(self, ownership_path: Path):
        """Same-process threads racing on add() must not overwrite each other."""
        s = OwnershipStore(ownership_path, "http://h")

        def worker(tid: int):
            for i in range(20):
                s.add("ds", f"t{tid}-{i}")

        threads = [Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        doc = json.loads(ownership_path.read_text("utf-8"))
        stored = set(doc["data"]["http://h"]["ds"])
        expected = {f"t{t}-{i}" for t in range(5) for i in range(20)}
        assert stored == expected


class TestSaveFailureDowngrade:
    def test_save_io_error_downgrades_to_warning(self, tmp_path: Path, monkeypatch):
        """D8/L4 regression: HTTP succeeds, disk full → warn, don't raise."""
        s = OwnershipStore(tmp_path / "x.json", "http://h")

        # Make _save_to_disk blow up with OSError.
        def boom(self):  # noqa: ARG001
            raise OSError("disk full")
        monkeypatch.setattr(OwnershipStore, "_save_to_disk", boom)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            s.add("ds", "sid")
            assert any(issubclass(w.category, RuntimeWarning) for w in caught)
        # In-memory state is still correct.
        assert s.contains("ds", "sid")


class TestAtomicWrite:
    def test_no_tmp_file_left_behind(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "sid")
        leftover = [p for p in ownership_path.parent.iterdir() if p.name.endswith(".tmp")]
        assert leftover == []
