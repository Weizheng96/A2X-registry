"""Synchronous example for ``A2XRegistryClient.reserve_blank_agents``.

Demonstrates the full team-formation flow under reservation locking:

  1. teammate registers itself as a blank/idle agent
  2. teamleader reserves the blank (filters: description=__BLANK__ AND
     status=online); the lease blocks parallel leaders for ``ttl_seconds``
  3. teamleader negotiates P2P with the teammate (mocked here)
  4. teammate replaces its card + sets status=busy; the SDK's auto-hook on
     ``replace_agent_card`` releases the lease at the same time
  5. when the team disbands, teammate calls ``restore_to_blank`` (also auto-
     releases any stray lease via the same hook)

Also shows: explicit context-manager release on negotiation failure, and
a second leader being blocked while the first holds the lease.

Run:
    python examples/client_sdk/reserve_blank_agents_sync.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from a2x_registry_client import A2XRegistryClient, ValidationError


def ensure_absent(client: A2XRegistryClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ds = "example_reserve_sync"

    teammate_owned = Path(tempfile.gettempdir()) / "a2x_example_reserve_teammate.json"
    leader_owned = Path(tempfile.gettempdir()) / "a2x_example_reserve_leader.json"
    print(f"Using backend: {base_url}")

    teammate = A2XRegistryClient(base_url=base_url, ownership_file=teammate_owned)
    leader_1 = A2XRegistryClient(base_url=base_url, ownership_file=leader_owned)
    leader_2 = A2XRegistryClient(base_url=base_url, ownership_file=False)

    try:
        ensure_absent(teammate, ds)
        teammate.create_dataset(ds)

        # 1) teammate registers itself as blank
        print("\n[1] teammate registers as blank")
        reg = teammate.register_blank_agent(
            ds, endpoint="http://teammate-endpoint", service_id="teammate_a",
        )
        sid = reg.service_id
        print(f"    blank registered: {sid}")

        # 2) leader_1 reserves the blank
        print("\n[2] leader_1 reserves 1 blank for 30s")
        with leader_1.reserve_blank_agents(ds, n=1, ttl_seconds=30) as r1:
            print(f"    holder_id={r1.holder_id}")
            print(f"    reserved sids: {[a['id'] for a in r1.agents]}")

            # 3) leader_2 tries to reserve — blocked, gets nothing
            print("\n[3] leader_2 tries to reserve concurrently — blocked")
            r2 = leader_2.reserve_blank_agents(ds, n=1, ttl_seconds=30)
            print(f"    leader_2 got {len(r2.agents)} agents (expected 0 — "
                  f"only blank is leased by leader_1)")
            leader_2.release_reservation(r2)  # tidy up (no-op if empty)

            # 4) negotiate P2P (mocked — assume success), teammate commits
            print("\n[4] P2P negotiate succeeds; teammate commits team card")
            print("    (replace_agent_card auto-releases the lease via "
                  "release_my_lease hook)")
            teammate.replace_agent_card(ds, sid, {
                "name": "Task Planner",
                "description": "active team member",
                "endpoint": "http://teammate-endpoint",
                "status": "busy",
            })

        # The `with` block already freed the lease (idempotent — no-op now)
        # because the auto-hook in replace_agent_card released it first.
        print("\n[5] context manager exit — no-op (auto-hook already released)")

        # 6) team disbands; teammate restores to blank
        print("\n[6] teammate restores to blank — back in idle pool")
        teammate.restore_to_blank(ds, sid)

        idle = teammate.list_idle_blank_agents(ds, n=10)
        print(f"    idle agents now: {[a['id'] for a in idle]}")

        # Demonstrate context-manager release-on-failure path
        print("\n[7] negotiation failure path: with-block frees lease on exit")
        try:
            with leader_1.reserve_blank_agents(ds, n=1, ttl_seconds=30) as r3:
                print(f"    reserved {[a['id'] for a in r3.agents]}")
                raise RuntimeError("simulated P2P negotiation failure")
        except RuntimeError as exc:
            print(f"    caught {type(exc).__name__}: {exc}")
        # Even after the exception, lease was released by __exit__
        idle_after = teammate.list_idle_blank_agents(ds, n=10)
        print(f"    idle agents after failure release: "
              f"{[a['id'] for a in idle_after]}")

        teammate.delete_dataset(ds)

    finally:
        teammate.close()
        leader_1.close()
        leader_2.close()


if __name__ == "__main__":
    main()
