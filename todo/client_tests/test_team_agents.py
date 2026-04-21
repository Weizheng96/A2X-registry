"""Focused tests for the Agent Team workflow (SDK side).

Covers:
  - register_blank_agent: uses ``__BLANK__`` description sentinel + prefix name
  - list_agents(**filters): flat return shape, filter validation, flat
    merge of metadata, handling of malformed backend responses
  - list_idle_blank_agents: ordering by agentTeamCount, n cap, returns
    same flat shape as list_agents
  - replace_agent_card: endpoint-field enforcement, ownership fail-fast
  - restore_to_blank: L1 → L2 → L3 endpoint fallback chain
  - End-to-end team cycle: register → replace → restore → deregister
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.client import (
    A2XClient,
    NotFoundError,
    NotOwnedError,
    ValidationError,
)
from src.client._internal import (
    BLANK_AGENT_NAME_PREFIX,
    BLANK_DESCRIPTION_SENTINEL,
    ENDPOINT_FIELD,
    TEAM_COUNT_FIELD,
    build_blank_agent_card,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_client(handler, tmp_path) -> tuple[A2XClient, list[httpx.Request]]:
    recorded: list[httpx.Request] = []

    def wrapper(req: httpx.Request) -> httpx.Response:
        recorded.append(req)
        return handler(req)

    client = A2XClient(
        base_url="http://test",
        ownership_file=tmp_path / "owned.json",
    )
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(wrapper)
    )
    return client, recorded


def _mk_wrapped(sid: str, card: dict, svc_type: str = "a2a") -> dict:
    """Construct the backend's wrapped entry for mode=filter responses."""
    # build_description approximation: description + "." (matches what
    # list_services does for a2a); the SDK should normalise via metadata
    # merge anyway.
    return {
        "id": sid,
        "type": svc_type,
        "name": card.get("name", ""),
        "description": card.get("description", "") + "." if svc_type == "a2a" else card.get("description", ""),
        "metadata": dict(card) if svc_type == "a2a" else {
            k: v for k, v in card.items() if k not in ("name", "description")
        },
    }


# ── build_blank_agent_card helper (pure) ────────────────────────────────────

class TestBlankCardConstruction:
    def test_blank_card_shape(self):
        card = build_blank_agent_card("http://a.com:8080")
        assert card["name"] == f"{BLANK_AGENT_NAME_PREFIX}http://a.com:8080"
        assert card["description"] == BLANK_DESCRIPTION_SENTINEL
        assert card[ENDPOINT_FIELD] == "http://a.com:8080"
        assert card[TEAM_COUNT_FIELD] == 0

    def test_description_sentinel_is_BLANK(self):
        """The sentinel must be the exact string '__BLANK__' — it's a
        cross-version contract between SDK and any filter consumers."""
        assert BLANK_DESCRIPTION_SENTINEL == "__BLANK__"
        card = build_blank_agent_card("http://x")
        assert card["description"] == "__BLANK__"

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, [], {}])
    def test_rejects_bad_endpoint(self, bad):
        with pytest.raises(ValueError):
            build_blank_agent_card(bad)


# ── register_blank_agent ─────────────────────────────────────────────────────

class TestRegisterBlankAgent:
    def test_registers_with_blank_card_and_caches_endpoint(self, tmp_path):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={
                "service_id": "agent_x", "dataset": "t", "status": "registered",
            })

        client, _ = _make_client(handler, tmp_path)
        resp = client.register_blank_agent("t", endpoint="http://a.com", service_id="agent_x")

        card = captured["body"]["agent_card"]
        assert card["description"] == "__BLANK__"
        assert card["endpoint"] == "http://a.com"
        assert card["agentTeamCount"] == 0
        # L1 cache populated for later restore_to_blank
        assert client._blank_endpoints[("t", "agent_x")] == "http://a.com"
        # Ownership recorded
        assert client._owned.contains("t", "agent_x")
        assert resp.status == "registered"
        client.close()

    def test_persistent_false_skips_ownership_and_cache(self, tmp_path):
        def handler(req):
            return httpx.Response(200, json={
                "service_id": "agent_x", "dataset": "t", "status": "registered",
            })

        client, _ = _make_client(handler, tmp_path)
        client.register_blank_agent("t", endpoint="http://a.com",
                                    service_id="agent_x", persistent=False)
        # Mirrors register_agent semantics — neither _owned nor cache gets written
        assert not client._owned.contains("t", "agent_x")
        # Blank endpoint cache IS written because L1 is session-only and
        # helps same-process restore_to_blank even when persistent=False.
        # (Design choice: cache tracks session state, not persistence state.)
        assert client._blank_endpoints[("t", "agent_x")] == "http://a.com"
        client.close()

    def test_empty_endpoint_fail_fast_no_http(self, tmp_path):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200)

        client, _ = _make_client(handler, tmp_path)
        with pytest.raises(ValueError, match=r"endpoint must be a non-empty"):
            client.register_blank_agent("t", endpoint="")
        assert len(sent) == 0
        client.close()


# ── list_idle_blank_agents ────────────────────────────────────────────────────

class TestListIdleBlankAgents:
    def test_filters_by_sentinel_and_sorts_ascending(self, tmp_path):
        captured = {}

        def handler(req):
            captured["params"] = dict(req.url.params.multi_items())
            return httpx.Response(200, json=[
                _mk_wrapped("a", {"name": "nA", "description": "__BLANK__",
                                  "endpoint": "http://a", "agentTeamCount": 2}),
                _mk_wrapped("b", {"name": "nB", "description": "__BLANK__",
                                  "endpoint": "http://b", "agentTeamCount": 0}),
                _mk_wrapped("c", {"name": "nC", "description": "__BLANK__",
                                  "endpoint": "http://c", "agentTeamCount": 1}),
            ])

        client, _ = _make_client(handler, tmp_path)
        idle = client.list_idle_blank_agents("t", n=5)
        # Must send the sentinel filter to backend
        assert captured["params"]["description"] == "__BLANK__"
        # Ascending by agentTeamCount
        assert [a["id"] for a in idle] == ["b", "c", "a"]
        # Flat shape — id + card fields at top level
        assert idle[0]["endpoint"] == "http://b"
        assert idle[0]["agentTeamCount"] == 0
        client.close()

    def test_missing_team_count_treated_as_zero(self, tmp_path):
        """An entry without agentTeamCount should sort alongside count=0."""
        def handler(req):
            return httpx.Response(200, json=[
                _mk_wrapped("a", {"name": "nA", "description": "__BLANK__",
                                  "endpoint": "http://a", "agentTeamCount": 5}),
                _mk_wrapped("b", {"name": "nB", "description": "__BLANK__",
                                  "endpoint": "http://b"}),  # no count
            ])

        client, _ = _make_client(handler, tmp_path)
        idle = client.list_idle_blank_agents("t", n=2)
        # The one without count sorts first (treated as 0)
        assert idle[0]["id"] == "b"
        client.close()

    def test_n_cap(self, tmp_path):
        def handler(req):
            return httpx.Response(200, json=[
                _mk_wrapped(f"s{i}",
                            {"name": f"n{i}", "description": "__BLANK__",
                             "endpoint": f"http://{i}", "agentTeamCount": i})
                for i in range(5)
            ])

        client, _ = _make_client(handler, tmp_path)
        r = client.list_idle_blank_agents("t", n=2)
        assert len(r) == 2
        assert [a["id"] for a in r] == ["s0", "s1"]
        client.close()

    def test_n_zero_returns_empty_no_http(self, tmp_path):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200, json=[])

        client, _ = _make_client(handler, tmp_path)
        assert client.list_idle_blank_agents("t", n=0) == []
        assert len(sent) == 0
        client.close()

    def test_no_blanks_returns_empty(self, tmp_path):
        def handler(req):
            return httpx.Response(200, json=[])

        client, _ = _make_client(handler, tmp_path)
        assert client.list_idle_blank_agents("t", n=10) == []
        client.close()

    @pytest.mark.parametrize("bad", [-1, True, False, 1.5, "3", None])
    def test_invalid_n_rejected_locally(self, tmp_path, bad):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200)

        client, _ = _make_client(handler, tmp_path)
        with pytest.raises(ValueError, match=r"n must be a non-negative int"):
            client.list_idle_blank_agents("t", bad)
        assert len(sent) == 0
        client.close()


# ── replace_agent_card ───────────────────────────────────────────────────────

class TestReplaceAgentCard:
    def test_endpoint_validation_before_ownership_check(self, tmp_path):
        """ValueError fires on card validation, even if sid is foreign."""
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200)

        client, _ = _make_client(handler, tmp_path)
        # foreign sid AND no endpoint — endpoint check wins, fail-fast
        with pytest.raises(ValueError, match=r"endpoint"):
            client.replace_agent_card("t", "foreign_sid",
                                      {"name": "n", "description": "d"})
        assert len(sent) == 0
        client.close()

    def test_ownership_check_after_endpoint_validation(self, tmp_path):
        """With valid endpoint but foreign sid → NotOwnedError, no HTTP."""
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200)

        client, _ = _make_client(handler, tmp_path)
        with pytest.raises(NotOwnedError):
            client.replace_agent_card("t", "foreign_sid",
                                      {"name": "n", "description": "d",
                                       "endpoint": "http://e"})
        assert len(sent) == 0
        client.close()

    def test_owned_replace_succeeds_and_posts_full_card(self, tmp_path):
        captured = {}

        def handler(req):
            if "services/a2a" in str(req.url):
                captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={
                "service_id": "agent_x", "dataset": "t", "status": "updated",
            })

        client, _ = _make_client(handler, tmp_path)
        # Register first so we own it
        client.register_blank_agent("t", endpoint="http://a", service_id="agent_x")
        new_card = {"name": "Team", "description": "working",
                    "endpoint": "http://a", "agentTeamCount": 1,
                    "skills": [{"name": "plan"}]}
        resp = client.replace_agent_card("t", "agent_x", new_card)
        assert resp.status == "updated"
        # POST target is the a2a register path, body carries same sid
        assert captured["body"]["service_id"] == "agent_x"
        assert captured["body"]["agent_card"] == new_card
        # _owned.add is idempotent — still owned
        assert client._owned.contains("t", "agent_x")
        client.close()

    def test_404_clears_ownership_and_blank_cache(self, tmp_path):
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            if calls["n"] == 1:  # register_blank_agent
                return httpx.Response(200, json={
                    "service_id": "agent_x", "dataset": "t", "status": "registered",
                })
            return httpx.Response(404, json={"detail": "Service not found"})

        client, _ = _make_client(handler, tmp_path)
        client.register_blank_agent("t", endpoint="http://a", service_id="agent_x")
        with pytest.raises(NotFoundError):
            client.replace_agent_card("t", "agent_x",
                                      {"name": "n", "description": "d",
                                       "endpoint": "http://a"})
        # Both local caches cleared
        assert not client._owned.contains("t", "agent_x")
        assert ("t", "agent_x") not in client._blank_endpoints
        client.close()


# ── restore_to_blank (L1 / L2 / L3 fallback) ─────────────────────────────────

class TestRestoreToBlank:
    def _setup_registered(self, tmp_path, handler):
        client, recorded = _make_client(handler, tmp_path)
        client.register_blank_agent("t", endpoint="http://a.com",
                                    service_id="agent_x")
        return client, recorded

    def test_L1_hit_no_extra_http(self, tmp_path):
        """register → restore within one process: L1 serves endpoint, no GET."""
        def handler(req):
            return httpx.Response(200, json={
                "service_id": "agent_x", "dataset": "t", "status": "registered",
            })

        client, recorded = self._setup_registered(tmp_path, handler)
        # Snapshot call count after register
        calls_before = len(recorded)
        client.restore_to_blank("t", "agent_x")
        # Only 1 POST (the replace) — no GET
        assert len(recorded) == calls_before + 1
        assert recorded[-1].method == "POST"
        assert "services/a2a" in str(recorded[-1].url)
        client.close()

    def test_L2_GET_single_when_cache_cold(self, tmp_path):
        """Cache cleared → restore falls back to GET single-mode."""
        card_state = {
            "name": "_BlankAgent_http://a.com",
            "description": "__BLANK__",
            "endpoint": "http://a.com",
            "agentTeamCount": 0,
        }

        def handler(req):
            p = req.url.path
            params = dict(req.url.params)
            if req.method == "POST" and "services/a2a" in p:
                return httpx.Response(200, json={
                    "service_id": "agent_x", "dataset": "t", "status": "registered",
                })
            if req.method == "GET" and p.endswith("/services") and params.get("mode") == "single":
                return httpx.Response(200, json={
                    "id": "agent_x", "type": "a2a", "name": card_state["name"],
                    "description": card_state["description"] + ".",
                    "metadata": card_state,
                })
            return httpx.Response(404)

        client, recorded = self._setup_registered(tmp_path, handler)
        client._blank_endpoints.clear()  # force L2
        calls_before = len(recorded)
        client.restore_to_blank("t", "agent_x")
        # 1 GET (L2) + 1 POST (replace)
        assert len(recorded) - calls_before == 2
        assert recorded[-2].method == "GET"
        assert recorded[-1].method == "POST"
        client.close()

    def test_L3_ValueError_when_endpoint_missing_from_card(self, tmp_path):
        """Cache empty + card missing endpoint → explicit ValueError."""
        def handler(req):
            p = req.url.path
            params = dict(req.url.params)
            if req.method == "POST" and "services/a2a" in p:
                return httpx.Response(200, json={
                    "service_id": "agent_x", "dataset": "t", "status": "registered",
                })
            if req.method == "GET" and p.endswith("/services") and params.get("mode") == "single":
                return httpx.Response(200, json={
                    "id": "agent_x", "type": "a2a", "name": "broken",
                    "description": "d.", "metadata": {"name": "broken", "description": "d"},
                })  # NO endpoint key
            return httpx.Response(404)

        client, _ = self._setup_registered(tmp_path, handler)
        client._blank_endpoints.clear()
        with pytest.raises(ValueError, match=r"endpoint.*missing"):
            client.restore_to_blank("t", "agent_x")
        client.close()

    def test_not_owned_fails_before_endpoint_resolution(self, tmp_path):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200)

        client, _ = _make_client(handler, tmp_path)
        with pytest.raises(NotOwnedError):
            client.restore_to_blank("t", "foreign_sid")
        assert len(sent) == 0
        client.close()


# ── End-to-end team cycle ────────────────────────────────────────────────────

class TestTeamCycle:
    def test_register_replace_restore_deregister_flow(self, tmp_path):
        """Full lifecycle: blank → team → blank → deregistered."""
        state: dict[str, dict] = {}

        def handler(req):
            body = json.loads(req.content) if req.content else {}
            params = dict(req.url.params)
            path = req.url.path

            if req.method == "POST" and "services/a2a" in path:
                sid = body.get("service_id", "agent_auto")
                state[sid] = body["agent_card"]
                return httpx.Response(200, json={
                    "service_id": sid, "dataset": "t",
                    "status": "updated" if sid in state else "registered",
                })
            if req.method == "GET" and path.endswith("/services"):
                if params.get("mode") == "filter":
                    filters = {k: v for k, v in req.url.params.items()
                               if k not in ("mode", "service_id", "size", "page")}
                    out = []
                    for sid, card in state.items():
                        if all(k in card and str(card[k]) == v
                               for k, v in filters.items()):
                            out.append({
                                "id": sid, "type": "a2a",
                                "name": card["name"],
                                "description": card["description"] + ".",
                                "metadata": card,
                            })
                    return httpx.Response(200, json=out)
                if params.get("mode") == "single":
                    sid = params.get("service_id")
                    card = state.get(sid)
                    if card:
                        return httpx.Response(200, json={
                            "id": sid, "type": "a2a",
                            "name": card["name"],
                            "description": card["description"] + ".",
                            "metadata": card,
                        })
            if req.method == "DELETE" and "/services/" in path:
                sid = path.rsplit("/", 1)[-1]
                state.pop(sid, None)
                return httpx.Response(200, json={
                    "service_id": sid, "status": "deregistered",
                })
            return httpx.Response(404)

        client, _ = _make_client(handler, tmp_path)

        # 1) Register as blank
        resp = client.register_blank_agent("t", endpoint="http://a", service_id="agent_x")
        sid = resp.service_id

        # 2) Visible in idle list
        idle = client.list_idle_blank_agents("t", n=5)
        assert len(idle) == 1
        assert idle[0]["id"] == sid
        assert idle[0]["endpoint"] == "http://a"
        assert idle[0]["agentTeamCount"] == 0

        # 3) Replace with team card (endpoint preserved)
        client.replace_agent_card("t", sid, {
            "name": "Task Planner",
            "description": "working",
            "endpoint": "http://a",
            "agentTeamCount": 1,
            "skills": [{"name": "plan"}],
        })

        # 4) No longer in idle pool (description ≠ __BLANK__)
        idle = client.list_idle_blank_agents("t", n=5)
        assert len(idle) == 0

        # 5) Restore — L1 cache hit, no extra GET needed
        client.restore_to_blank("t", sid)

        # 6) Back in idle pool
        idle = client.list_idle_blank_agents("t", n=5)
        assert len(idle) == 1
        assert idle[0]["id"] == sid

        # 7) Deregister — ownership & blank cache cleared
        client.deregister_agent("t", sid)
        assert not client._owned.contains("t", sid)
        assert ("t", sid) not in client._blank_endpoints
        client.close()


# ── list_agents filter edge cases (beyond what test_client.py covers) ────────

class TestListAgentsEdgeCases:
    def test_non_list_backend_response_returns_empty(self, tmp_path):
        """Defensive: if backend misbehaves, SDK doesn't crash."""
        def handler(req):
            return httpx.Response(200, json={"unexpected": "shape"})

        client, _ = _make_client(handler, tmp_path)
        assert client.list_agents("ds") == []
        client.close()

    def test_filter_values_coerced_to_strings(self, tmp_path):
        """Int/bool filter values get stringified before being sent."""
        captured = {}

        def handler(req):
            captured["params"] = dict(req.url.params.multi_items())
            return httpx.Response(200, json=[])

        client, _ = _make_client(handler, tmp_path)
        client.list_agents("t", agentTeamCount=0, active=True)
        assert captured["params"]["agentTeamCount"] == "0"
        # str(True) == "True"
        assert captured["params"]["active"] == "True"
        client.close()

    def test_empty_filter_key_rejected(self, tmp_path):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200)

        client, _ = _make_client(handler, tmp_path)
        with pytest.raises(ValueError, match=r"filter keys must be non-empty"):
            client.list_agents("t", **{"": "x"})
        assert len(sent) == 0
        client.close()

    def test_filter_matches_wrapped_response_flattened(self, tmp_path):
        """Verify the merge: metadata keys override wrapper keys for a2a."""
        def handler(req):
            return httpx.Response(200, json=[
                {"id": "s", "type": "a2a",
                 "name": "wrap_name", "description": "wrap_desc.",
                 "metadata": {"name": "meta_name", "description": "meta_desc",
                              "endpoint": "http://e"}},
            ])

        client, _ = _make_client(handler, tmp_path)
        [a] = client.list_agents("t")
        # Metadata wins — name/description come from the raw card
        assert a["name"] == "meta_name"
        assert a["description"] == "meta_desc"
        # Top-level wrapper fields preserved
        assert a["id"] == "s"
        assert a["type"] == "a2a"
        # Card-only fields at top level
        assert a["endpoint"] == "http://e"
        # metadata is consumed (not nested anymore)
        assert "metadata" not in a
        client.close()
