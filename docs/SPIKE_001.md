# Critical Spike 001 — ChatGPT.com → local MindContinuum bridge

> **Goal:** prove the minimum loop:
>
> ChatGPT.com → HTTPS tunnel → local Windows MindContinuum (Streamable HTTP
> MCP) → SQLite write/read → ChatGPT.com receives the record back.

This document is the owner-facing playbook for Gate 3.

---

## Architecture

```
[ChatGPT.com Web]
       │  HTTPS
       ▼
[ Public HTTPS endpoint ]  ← tunnel client on Windows
       │  HTTP loopback
       ▼
[ MindContinuum on Windows  ]   http://127.0.0.1:3780
   ├─ /            dashboard (vanilla HTML/JS)
   ├─ /api/*       REST  (dashboard talks to this)
   └─ /mcp         Streamable HTTP MCP   ← ChatGPT talks to this
                       │
                       ▼
                 SQLite + FTS5
```

The MCP server is mounted on the same FastAPI process; only one port has to
be exposed by the tunnel.

---

## What the agent already validated locally (macOS, Gate 1)

- `scripts/mcp_inspect.py` connects to `http://127.0.0.1:3780/mcp/`,
  initializes, lists 8 tools, calls `save_memory`, then calls
  `search_memory` and receives the record back.
- The same record appears in the dashboard Inbox and in the JSON export.
- `pytest` passes 40 tests, including a guard test that fails if any
  destructive MCP tool ever sneaks in.

What is NOT yet validated and must be validated by the owner: the **tunnel**
and the **ChatGPT.com connector UI**.

---

## Preferred transport options (try in order)

### Option A — Cloudflare Tunnel (recommended)

Most stable across networks. No paid account required for short-lived
"quick" tunnels.

```bat
:: One-time install (winget):
winget install --id Cloudflare.cloudflared

:: In one terminal: start MindContinuum
run_windows.bat

:: In a second terminal: start a quick tunnel to the local server
cloudflared tunnel --url http://127.0.0.1:3780
```

`cloudflared` prints a public URL of the form
`https://<random>.trycloudflare.com`. The MCP endpoint to give ChatGPT is:

```
https://<random>.trycloudflare.com/mcp
```

Quick tunnels rotate the URL on every restart. For a stable URL, create a
named tunnel and bind a domain — see the Cloudflare docs.

### Option B — ngrok

```bat
:: One-time:
winget install ngrok.ngrok
ngrok config add-authtoken <your token>

:: Start MindContinuum, then:
ngrok http 3780
```

Take the `https://<id>.ngrok-free.app` URL and append `/mcp`.

### Option C — Secure MCP Tunnel (preview)

If the project owner has access to the Secure MCP Tunnel preview, the
tunnel client is configured the same way: it terminates HTTPS publicly
and forwards to `http://127.0.0.1:3780`. Use `/mcp` for the connector URL.

> **Per the project access policy:** the agent will NOT request the
> owner's ChatGPT sign-in. The agent may set up its own tunnel-client
> credentials for the developer side of the spike if needed; the owner
> performs the final ChatGPT connector click-test.

---

## Connecting from ChatGPT.com

> The exact menu names in ChatGPT.com change over time. The current shape
> (as of writing) is: **Settings → Connectors → Add connector → Custom
> MCP server**. If the option is not visible, enable **Developer mode** in
> Settings first.

1. Open ChatGPT.com and sign in.
2. Open **Settings → Connectors** (or **Beta features → Custom connectors**
   if visible). Enable Developer mode if asked.
3. Click **Add custom connector** (or equivalent "Add MCP server").
4. Enter:
   - **Name:** `MindContinuum`
   - **URL:** the `/mcp` URL from your tunnel (see options above)
   - **Auth:** none (v1 has no token; the tunnel itself is the trust
     boundary — see Security notes below)
5. Save. ChatGPT should show the 8 MindContinuum tools.

---

## Success criteria (must all pass)

| # | Step | Expected |
|---|---|---|
| 1 | In ChatGPT: *"Save memory: MindContinuum tunnel test works."* | ChatGPT calls `save_memory` and returns the new id. |
| 2 | Open the dashboard at `http://127.0.0.1:3780/` | New item appears in **Inbox**. |
| 3 | Open the **Log** tab | Row with `action=memory.create`, `actor=mcp`. |
| 4 | In ChatGPT: *"Search memory for tunnel test."* | ChatGPT calls `search_memory`, displays the record body. |
| 5 | Open `data/mindcontinuum.sqlite` (DB Browser for SQLite) | Row visible in `memory_items`. |

---

## Failure triage

| Symptom | Likely cause | What to send back |
|---|---|---|
| ChatGPT does not see the connector / option missing | Developer mode not enabled, or feature not rolled out yet | Screenshot of Settings → Connectors |
| Connector save fails with 4xx | Wrong URL (missing `/mcp`), HTTP instead of HTTPS | Connector URL, server log |
| Connector saves but tools list is empty | MCP transport mismatch — tunnel forwarding broken | `curl -i https://<tunnel>/mcp/`, server log |
| ChatGPT calls tool but you see no log entry | Tunnel hits wrong port / process | Tunnel client log, `run_windows.bat` console |
| Item is created but search returns nothing | FTS5 trigger broken (unlikely — covered by tests) | `dashboard Inbox screenshot`, `pytest` output |

---

## Security notes for the spike

- v1 has **no MCP token**. The tunnel client decides who reaches your
  laptop. Treat the tunnel URL as a secret — rotate it after the spike.
- The MCP surface is read/write but has **no destructive operations**.
  A leaked tunnel URL would allow someone to write memories but not delete
  or query arbitrary SQL.
- All writes are recorded in `events_log` — visible in the dashboard Log
  tab. Review that tab during the spike to confirm only your calls landed.
- To revoke: stop the tunnel client (`Ctrl+C` in the cloudflared/ngrok
  window), or run `cloudflared tunnel cleanup <tunnel>` for a named tunnel.

---

## Output to capture for the report

When the spike succeeds, send back:

- Transport used (Cloudflare / ngrok / Secure MCP Tunnel / other).
- The public URL form (with the `/mcp` suffix).
- Screenshot of ChatGPT successfully calling `save_memory` and
  `search_memory` with the same body roundtripped.
- Screenshot of the dashboard Inbox showing the new item.
- The first three rows of the **Log** tab (action / actor / target_id).

When the spike fails, send back:

- Which step in the success table broke first.
- Server log (stdout of `run_windows.bat`).
- Tunnel client log line for the failing request.
- Whether retrying with a different transport changes anything.
