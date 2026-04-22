"""Smoke runner — executes every example script and reports per-file status.

Each example is run as a subprocess so a failure in one doesn't abort the rest.
Useful as a CI smoke test against a running backend.

Run:
    python examples/client_sdk/run_all_examples.py

Optional environment variables (forwarded to each example):
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]


# Order matters loosely: dataset CRUD before service CRUD before team-agent flow.
EXAMPLE_ORDER: list[str] = [
    "create_dataset_sync.py",
    "create_dataset_async.py",
    "delete_dataset_sync.py",
    "delete_dataset_async.py",

    "register_agent_sync.py",
    "register_agent_async.py",
    "update_agent_sync.py",
    "update_agent_async.py",
    "set_status_sync.py",
    "set_status_async.py",

    "list_agents_sync.py",
    "list_agents_async.py",
    "get_agent_sync.py",
    "get_agent_async.py",

    "register_blank_agent_sync.py",
    "register_blank_agent_async.py",
    "list_idle_blank_agents_sync.py",
    "list_idle_blank_agents_async.py",
    "replace_agent_card_sync.py",
    "replace_agent_card_async.py",
    "restore_to_blank_sync.py",
    "restore_to_blank_async.py",
    "reserve_blank_agents_sync.py",
    "reserve_blank_agents_async.py",

    "deregister_agent_sync.py",
    "deregister_agent_async.py",
]


def _run_one(path: Path) -> tuple[bool, str]:
    """Run an example, returning (ok, tail_of_output)."""
    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (120s)"
    output = (result.stdout + result.stderr).strip()
    tail = "\n".join(output.splitlines()[-5:]) if output else "<no output>"
    return result.returncode == 0, tail


def main() -> int:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    print(f"Running examples against {base_url}")
    print(f"  ({len(EXAMPLE_ORDER)} files)\n")

    failures: list[str] = []
    for name in EXAMPLE_ORDER:
        path = HERE / name
        if not path.exists():
            print(f"  SKIP   {name}  (not found)")
            continue
        ok, tail = _run_one(path)
        marker = "OK" if ok else "FAIL"
        print(f"  {marker:<6} {name}")
        if not ok:
            failures.append(name)
            print(f"    └── tail: {tail}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} / {len(EXAMPLE_ORDER)}")
        for name in failures:
            print(f"  - {name}")
        return 1
    print(f"PASSED: {len(EXAMPLE_ORDER)} / {len(EXAMPLE_ORDER)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
