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

    def test_uses_os_replace_for_atomic_rename(self, ownership_path: Path, monkeypatch):
        """Regression: must go through os.replace, never plain rename / write."""
        calls = {"replace": 0}
        original = os.replace

        def tracker(src, dst):
            calls["replace"] += 1
            return original(src, dst)

        monkeypatch.setattr(os, "replace", tracker)
        s = OwnershipStore(ownership_path, "http://h")
        s.add("ds", "sid")
        assert calls["replace"] >= 1

    def test_parent_dirs_auto_created(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c" / "owned.json"
        assert not deep.parent.exists()
        s = OwnershipStore(deep, "http://h")
        s.add("ds", "sid")
        assert deep.exists()
        assert deep.parent.is_dir()


class TestLoadingEdgeCases:
    def test_empty_file_tolerated(self, ownership_path: Path):
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text("")  # 0 bytes
        s = OwnershipStore(ownership_path, "http://h")
        assert not s.contains("ds", "sid")

    def test_base_url_missing_in_file_is_clean_start(self, ownership_path: Path):
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text(json.dumps({
            "schema_version": 1,
            "data": {"http://OTHER": {"ds": ["x"]}}
        }))
        s = OwnershipStore(ownership_path, "http://ME")
        assert not s.contains("ds", "x")
        # File must not have been rewritten just from reading
        doc = json.loads(ownership_path.read_text("utf-8"))
        assert doc["data"] == {"http://OTHER": {"ds": ["x"]}}

    def test_future_schema_version_falls_back_to_empty(self, ownership_path: Path):
        """Unknown future schema versions should not crash; treat as clean start."""
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text(json.dumps({
            "schema_version": 99,
            "data": {"http://h": {"ds": ["x"]}}
        }))
        s = OwnershipStore(ownership_path, "http://h")
        assert not s.contains("ds", "x")  # ignored, forward-compat safe

    def test_load_does_not_create_file(self, tmp_path: Path):
        """Constructor should never create the file by itself."""
        path = tmp_path / "owned.json"
        assert not path.exists()
        OwnershipStore(path, "http://h")
        assert not path.exists()  # still no file after construction

    def test_garbage_values_ignored(self, ownership_path: Path):
        """Non-string sids and non-list buckets are silently dropped."""
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text(json.dumps({
            "schema_version": 1,
            "data": {"http://h": {
                "ds1": ["valid", 42, None, {"x": 1}],  # non-strings dropped
                "ds2": "not a list",                    # bucket dropped entirely
            }}
        }))
        s = OwnershipStore(ownership_path, "http://h")
        assert s.contains("ds1", "valid")
        assert not s.contains("ds1", "42")
        assert not s.contains("ds2", "anything")


class TestUnicodeKeys:
    def test_unicode_dataset_name(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.add("研究团队", "agent_研究_001")
        assert s.contains("研究团队", "agent_研究_001")

        reload = OwnershipStore(ownership_path, "http://h")
        assert reload.contains("研究团队", "agent_研究_001")

        # File must be valid UTF-8
        doc = json.loads(ownership_path.read_text("utf-8"))
        assert "研究团队" in doc["data"]["http://h"]


class TestContainsInvariant:
    def test_contains_returns_false_for_missing_dataset(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        assert not s.contains("never_existed", "sid")

    def test_remove_on_missing_bucket_is_silent(self, ownership_path: Path):
        s = OwnershipStore(ownership_path, "http://h")
        s.remove("never_existed", "sid")  # must not raise
        assert not ownership_path.exists()  # no spurious write
