# MindContinuum

Local-first long-term memory for AI conversations.

MindContinuum runs a small server on your own machine that exposes a safe set
of **MCP tools** to ChatGPT (and other MCP-compatible clients), stores notes,
decisions, tasks, and project context in a local **SQLite** database with
**FTS5** full-text search, and ships with a tiny local dashboard for review,
edit, and archive.

Nothing leaves your laptop unless you explicitly export it.

```
ChatGPT.com  ─►  HTTPS tunnel  ─►  Local MindContinuum  ─►  SQLite (FTS5)
                                          │
                                          └─►  Local dashboard (UI + REST)
```

---

## 1 · 2 · 3 setup

### 1. Get the code

```bash
git clone https://github.com/a-saven/mindcontinuum.git
cd mindcontinuum
```

### 2. Run

**macOS / Linux:**

```bash
./run.sh
```

**Windows:**

Double-click `run_windows.bat`, or run from a `cmd` prompt:

```bat
run_windows.bat
```

The first run creates `.venv`, installs dependencies, and starts the server.
Subsequent runs reuse the venv.

### 3. Connect

Open the dashboard at <http://127.0.0.1:3780/> and copy the **MCP endpoint**
from the top-right (`http://127.0.0.1:3780/mcp`). To make ChatGPT.com reach
your machine you need an HTTPS tunnel — see
[docs/SPIKE_001.md](docs/SPIKE_001.md).

---

## What you get

| Capability | Where |
|---|---|
| Local SQLite store with FTS5 search | `data/mindcontinuum.sqlite` (or `~/.mindcontinuum/...`) |
| Safe MCP tools (no SQL, no delete) | `POST /mcp` |
| REST API (UI uses it) | `/api/*` |
| Dashboard (inbox, search, decisions, tasks, log, settings) | `/` |
| JSON + Markdown export | `/api/export/json`, `/api/export/markdown` |
| Write-log of every action | `/api/events` + Log tab |

## Safe MCP tools

Only these tools are exposed to AI clients in v1. **No raw SQL, no delete,
no schema mutation, no arbitrary filesystem access.**

| Tool | Purpose |
|---|---|
| `ping` | Sanity check; returns server name + UTC time. |
| `save_memory` | Create a memory item. Defaults to `status=inbox`. |
| `search_memory` | FTS5 search across title / summary / body / tags. |
| `get_memory` | Fetch one item by id. |
| `list_recent` | Most recently created items. |
| `record_decision` | Decision + rationale + tradeoffs, linked memory item. |
| `record_task` | Task + next action + priority, linked memory item. |
| `append_memory` | Append timestamped text to an existing body. |

Inputs are validated against allowed enums (type, status, importance,
priority). Every write goes through `events_log`, viewable in the **Log** tab.

## Memory data model (v1)

`memory_items` is the central table; everything else hangs off it.

- `memory_items` (id, title, type, summary, body, project_id, source,
  tags_json, importance, status, created_at, updated_at)
- `projects`
- `decisions` — proposed → accepted/superseded/rejected
- `tasks` — open → in_progress / blocked / done / cancelled
- `sources`, `memory_links`, `events_log`
- `memory_items_fts` — FTS5 virtual table mirrored via triggers

Allowed enums:

- **type**: `note`, `idea`, `research`, `project_context`, `client_context`,
  `prompt`, `decision`, `task`, `risk`
- **status**: `inbox`, `processed`, `stable`, `pinned`, `archived`, `stale`,
  `rejected`
- **importance**: `low`, `medium`, `high`

## Configuration

Environment variables (all optional):

| Name | Default | Notes |
|---|---|---|
| `MINDCONTINUUM_DATA_DIR` | `%LOCALAPPDATA%\MindContinuum` on Win; `~/.mindcontinuum` else | Holds the SQLite file. |
| `MINDCONTINUUM_DB` | `<data dir>/mindcontinuum.sqlite` | Override the DB path directly. |
| `MINDCONTINUUM_HOST` | `127.0.0.1` | Bind host. |
| `MINDCONTINUUM_PORT` | `3780` | Bind port. |
| `MINDCONTINUUM_MCP_MOUNT` | `/mcp` | MCP mount path. |

CLI overrides:

```bash
./run.sh --host 127.0.0.1 --port 3780 --data-dir ./data
```

## Tests

```bash
.venv/bin/python -m pytest
```

Tests cover:

- Core safe operations (save / search / get / list / append / archive / record_decision / record_task)
- Tag normalization, enum validation, empty input rejection
- FTS5 ranked search and punctuation handling
- REST API end-to-end (FastAPI TestClient)
- MCP tool surface (forbidden tools NOT exposed; required tools present)
- Event log captures every write

`pytest` is required to be green **before** any tunnel/connector validation
per the spec's DoD.

## Gates & handoff

This repo follows the staged gates from the project spec:

- **Gate 1 — Local code works on dev machine.** ✅ Implemented on macOS/Linux;
  ready to run on Windows.
- **Gate 2 — Windows target validation.** Owner action required. See
  [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md).
- **Gate 3 — ChatGPT.com connector validation.** Owner action required. See
  [docs/SPIKE_001.md](docs/SPIKE_001.md).
- **Gate 4 — Full product build.** Only after Gates 1–3 are green or waived.

See [docs/GATES.md](docs/GATES.md) for the full gate map and what gets
delivered at each step.

## Roadmap (post-spike)

Already drafted in [docs/ROADMAP.md](docs/ROADMAP.md). Not implemented in v1
on purpose — the spec is explicit: **do not overbuild before the first loop
works**.

- Local embeddings + hybrid search
- Duplicate detection + contradiction markers
- Project memory packs (`get_project_context_pack`)
- Markdown import
- Backup scheduler
- Optional Postgres backend
- Optional packaged desktop launcher

## Project layout

```
mindcontinuum/
├── README.md
├── pyproject.toml
├── requirements.txt
├── run.sh                  # macOS/Linux launcher
├── run_windows.bat         # Windows launcher
├── scripts/
│   └── mcp_inspect.py      # Local MCP round-trip probe
├── src/mindcontinuum/
│   ├── schema.sql          # SQLite + FTS5 + triggers
│   ├── db.py               # Connection + transactions
│   ├── core.py             # Safe memory operations (the ONLY write surface)
│   ├── mcp_tools.py        # FastMCP tool layer (no raw SQL, no delete)
│   ├── api.py              # FastAPI REST routes
│   ├── server.py           # ASGI app factory + uvicorn entry
│   ├── config.py
│   ├── static/             # Dashboard CSS + JS (vanilla)
│   └── templates/          # Dashboard HTML
├── tests/                  # pytest
├── docs/
│   ├── GATES.md
│   ├── SPIKE_001.md
│   ├── WINDOWS_SETUP.md
│   └── ROADMAP.md
└── data/                   # SQLite lives here when MINDCONTINUUM_DATA_DIR is unset
```

## License

MIT.
