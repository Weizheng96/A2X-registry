# A2X Registry Client SDK

Python client for the [A2X Registry](../../README.md) FastAPI backend. Primary
use case: **Agent Team registration and discovery** — each team member is
registered as an [A2A Agent Card v0.0](../register/validation.py) into a shared
dataset and managed (updated, queried, deregistered) through this SDK.

## Install

```bash
pip install httpx  # only runtime dependency
```

When packaged standalone:

```bash
pip install a2x-registry-client
```

## Quickstart (sync)

```python
from src.client import A2XClient          # or: from a2x_client import A2XClient

with A2XClient(base_url="http://127.0.0.1:8000") as client:
    client.create_dataset("research_team")   # formats defaults to {"a2a": "v0.0"}

    planner = client.register_agent("research_team", {
        "protocolVersion": "0.0",
        "name": "Task Planner",
        "description": "拆解复杂任务为可执行子任务",
    })

    client.set_status("research_team", planner.service_id, "online")
    client.update_agent("research_team", planner.service_id,
                        {"description": "updated desc"})

    for brief in client.list_agents("research_team"):
        print(brief.id, "-", brief.name)

    client.deregister_agent("research_team", planner.service_id)
    client.delete_dataset("research_team")
```

## Quickstart (async)

```python
import asyncio
from src.client import AsyncA2XClient

async def main():
    async with AsyncA2XClient(base_url="http://127.0.0.1:8000") as client:
        await client.create_dataset("research_team")
        resp = await client.register_agent("research_team", {
            "protocolVersion": "0.0", "name": "a", "description": "b",
        })
        print(resp.service_id)

asyncio.run(main())
```

## Ownership model

The SDK tracks which services **this client** registered. Mutating calls
(`update_agent`, `set_status`, `deregister_agent`) require the
`service_id` to be in that tracker, otherwise `NotOwnedError` is raised
**before** any HTTP request is sent.

Ownership is persisted to `~/.a2x_client/owned.json` by default (atomic
writes after every change, segmented by `base_url`). Disable with
`ownership_file=False` or override the path with `ownership_file="..."`.

## Exceptions

All errors inherit from `A2XError`. HTTP errors (`A2XHTTPError`) carry
`status_code` and `payload`; the local ownership error (`NotOwnedError`)
does not.

- `NotFoundError` — 404
- `ValidationError` — 400 / 422
- `UserConfigDeregisterForbiddenError` — 400 (subclass of `ValidationError`)
- `UnexpectedServiceTypeError` — `get_agent` received a non-JSON payload
- `ServerError` — 5xx
- `A2XConnectionError` — transport failure (timeout, connect refused, ...)

## Design

See [`todo/client_design.md`](../../todo/client_design.md) for the full
architecture, class diagram, and per-method sequence diagrams.
