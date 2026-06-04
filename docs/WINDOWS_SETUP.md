# Windows setup checklist — Gate 2

Owner-facing. Run through this on the target Windows 10/11 machine before
Gate 3.

## 1. Prerequisites

- [ ] **Python 3.11+** installed.
  - Recommended: install from <https://www.python.org/downloads/windows/>
    and check **"Add python.exe to PATH"** during install.
  - Verify: open `cmd` and run `py -3 --version` — should print `Python
    3.11.x` or newer.
- [ ] **Git for Windows** (only if you want to `git pull` updates; not
  strictly required if you downloaded a ZIP).
- [ ] Firewall allows `python.exe` to bind `127.0.0.1:3780`. This is
  loopback only, so Defender usually does not prompt — but corporate AV
  may. If a prompt appears, **Allow access (private network)**.
- [ ] (For Gate 3 only) a tunnel client — Cloudflare `cloudflared` or
  `ngrok`. See [SPIKE_001.md](SPIKE_001.md).

## 2. Get the code

Option A — `git`:

```bat
git clone https://github.com/a-saven/mindcontinuum.git
cd mindcontinuum
```

Option B — ZIP: download from the GitHub repo, extract, open `cmd` in
the extracted folder.

## 3. First run

Double-click `run_windows.bat` **or** from `cmd`:

```bat
run_windows.bat
```

Expected console output:

```
[mindcontinuum] creating virtual environment .venv
[mindcontinuum] installing dependencies (first run)

========================================
 MindContinuum - local memory server
 UI/API : http://127.0.0.1:3780/
 MCP    : http://127.0.0.1:3780/mcp
 Ctrl+C to stop
========================================

INFO:     Uvicorn running on http://127.0.0.1:3780 (Press CTRL+C to quit)
```

If you see `ERROR: Python 3.11+ not found`, install Python and re-open
`cmd` (PATH only refreshes for new sessions).

## 4. Verify the dashboard

- [ ] Open <http://127.0.0.1:3780/>. The page loads, the dot top-left is
  **green**, and the top-right shows `http://127.0.0.1:3780/mcp`.
- [ ] Click **+ New**, set Title = `"hello"`, click **Save**. The card
  appears in the Inbox.
- [ ] Click the card. Edit Body. Click **Save**. The change persists on
  refresh.
- [ ] Open the **Log** tab. You see `memory.create` and `memory.update`
  rows with actor `ui`.

## 5. Verify the MCP loop locally (no ChatGPT yet)

In a second terminal, with the server still running:

```bat
.venv\Scripts\python.exe scripts\mcp_inspect.py
```

Expected output:

```
[connected] MindContinuum v1.27.2
[tools] 8: ping, save_memory, search_memory, get_memory, list_recent, record_decision, record_task, append_memory
[ping] {...'status': 'pong'...}
[save_memory] -> { "id": ..., "title": "MindContinuum tunnel test works", ... }
[search_memory] -> { "count": 1, "results": [ ... ] }
```

Refresh the dashboard — the new "MindContinuum tunnel test works" item is
in the Inbox.

## 6. Choose where the database lives

Default: `%LOCALAPPDATA%\MindContinuum\mindcontinuum.sqlite`.

To put it somewhere else, set `MINDCONTINUUM_DATA_DIR` before launching:

```bat
set MINDCONTINUUM_DATA_DIR=D:\memory
run_windows.bat
```

Or pass `--data-dir`:

```bat
.venv\Scripts\python.exe -m mindcontinuum --data-dir D:\memory
```

## 7. Stop / restart

- **Stop:** `Ctrl+C` in the server window.
- **Restart:** run `run_windows.bat` again — venv is reused.
- **Wipe:** delete the `.venv` folder and the SQLite file under
  `%LOCALAPPDATA%\MindContinuum`. Next run rebuilds from scratch.

## 8. Report back

If everything above passes:

> ✅ Gate 2 PASS — dashboard works, mcp_inspect.py round-trip works.

If anything fails, send back:

1. The exact step that failed (1–7).
2. The full console output of `run_windows.bat`.
3. The output of `py -3 --version` and `where python`.
4. The contents of `%TEMP%\mc.log` if it exists.
