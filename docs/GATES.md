# Gates and owner handoff

This is the working map of where the project is, where it's headed, and where
owner action is required. It mirrors the gate definitions in the project
spec.

---

## Gate 1 — Local code works on developer machine ✅

**Status:** complete (macOS, with venv + pytest).

What's done:

- SQLite schema (`memory_items`, `projects`, `decisions`, `tasks`, `sources`,
  `memory_links`, `events_log`) bootstraps on first run.
- FTS5 virtual table mirrors `memory_items` via triggers, ranks results with
  `bm25()`.
- Safe core memory operations: `save_memory`, `search_memory`, `get_memory`,
  `list_recent`, `record_decision`, `record_task`, `append_memory`,
  `update_memory`, `archive_memory`.
- Every write goes through `events_log`.
- FastAPI dashboard (Inbox / Search / Decisions / Tasks / Log / Settings) +
  JSON + Markdown export.
- MCP server (FastMCP, Streamable HTTP transport) mounted at `/mcp`, exposing
  only the safe tools listed above. Validated end-to-end with
  `scripts/mcp_inspect.py`.
- `pytest`: **40/40 passing**, including a guard test that fails if any
  destructive tool (`delete_memory`, `drop`, `execute_sql`, …) is exposed.

Reproduce on a dev machine:

```bash
git clone https://github.com/a-saven/mindcontinuum.git
cd mindcontinuum
./run.sh                 # creates venv, installs deps, runs server
# in another shell:
.venv/bin/python -m pytest
.venv/bin/python scripts/mcp_inspect.py
```

---

## Gate 2 — Windows target validation ⏳ owner action

**This requires running on Oleg's Windows machine.**

What the agent CANNOT verify from macOS:

- That `run_windows.bat` boots without manual edits.
- That Python 3.11+ is picked up by `py -3` or `python` on PATH.
- That Windows Defender / corporate AV doesn't quarantine the bundled
  SQLite WAL files.
- That `127.0.0.1:3780` is reachable from a browser on the same machine.
- That `%LOCALAPPDATA%\MindContinuum\mindcontinuum.sqlite` is created with
  the right ACLs.
- That a Streamable HTTP MCP client on the same machine can complete the
  loop (the `mcp_inspect.py` test).

**Owner checklist:** [WINDOWS_SETUP.md](WINDOWS_SETUP.md).

**Pass criteria:**

1. `run_windows.bat` finishes startup and prints the MCP URL.
2. Browser at <http://127.0.0.1:3780/> shows the dashboard with the green dot.
3. `py -3 scripts\mcp_inspect.py` prints `[connected]` and the saved item
   appears in the dashboard Inbox.

If any step fails, copy the console output and the file at `%TEMP%\mc.log`
back to the agent — do NOT keep debugging blind.

---

## Gate 3 — ChatGPT.com connector validation ⏳ owner action

**Requires the owner's ChatGPT account. Do NOT share sign-in with the agent.**

Two pieces have to come together:

1. **A public HTTPS tunnel** terminating at the running Windows MindContinuum
   instance. Three known-working options are documented in
   [SPIKE_001.md](SPIKE_001.md) — Cloudflare Tunnel, ngrok, or the Secure
   MCP Tunnel preview.
2. **Connector setup in ChatGPT.com** (Settings → Connectors → Add custom
   connector → MCP) using the tunnel URL.

**Pass criteria (from the spec's Critical Spike 001):**

1. User says in ChatGPT: *"Save memory: MindContinuum tunnel test works."*
2. ChatGPT calls `save_memory`.
3. The record appears in local SQLite (visible in the dashboard Inbox).
4. User says in ChatGPT: *"Search memory for tunnel test."*
5. ChatGPT calls `search_memory`.
6. ChatGPT receives and displays the saved record.

**On failure**, capture:

- The exact connector setup screen (screenshot, secrets redacted).
- The tunnel client log line corresponding to the failing request.
- The MindContinuum server log (stdout of the run script).
- Which transport was tried (Cloudflare / ngrok / Secure MCP Tunnel /
  other).

Then report back which of the four failure modes applies:

- account/access
- tunnel setup
- MCP transport / protocol
- Windows environment
- tool schema

---

## Gate 4 — Full build resumes ⏸ blocked on Gate 3

Once Gate 3 is green (or explicitly waived by the project owner), the agent
proceeds with the rest of the spec:

- Stable / contradiction / consolidation workflows.
- Local embeddings + hybrid search.
- Project memory packs.
- Backup scheduler, Markdown import.
- Optional Postgres backend.
- Optional packaged desktop launcher.

See [ROADMAP.md](ROADMAP.md) for the staged plan after Gate 3.
