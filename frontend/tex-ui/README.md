# LIARA Textual Frontend Clone (frontend/tex-ui)

This folder contains a cloned Textual chat frontend extracted from `services/cli/textual_chat`.

Goal:

- keep the original behavior
- run independently from the service CLI package
- stay aligned with SSE behavior documented in `docs/API_REFERENCE.md`
- provide a second UI level for local workspace browsing

## Start (PowerShell)

```powershell
python .\frontend\tex-ui\main.py --base-url http://127.0.0.1:8010 --mode stream
```

Optional workspace root override:

```powershell
python .\frontend\tex-ui\main.py --workspace-root C:\ai\LIARA
```

WSL sandbox and optional controlled session binding:

```powershell
python .\frontend\tex-ui\main.py `
  --sandbox-root /home/liara/workspace `
  --workspace-session-id sess-0123456789abcdef
```

Default workspace behavior:

- `--workspace-root` defaults to WSL `\/home\/liara` via the Windows UNC path.
- Candidate paths are `\\wsl.localhost\Debian\home\liara`, `\\wsl$\Debian\home\liara` and Alpine fallbacks.
- Can be overridden with `--workspace-root`, `LIARA_HOME`, or `LIARA_WORKSPACE_ROOT`.

Default user behavior:

- `--user-id` defaults to the current Windows user (`USERNAME`).
- Can be overridden explicitly with `--user-id` or via `LIARA_USER_ID`.

## Views

- `Chat` tab: existing LIARA chat with sync/stream mode.
- `Workspace` tab: local explorer for the LIARA home with file preview.

Keyboard:

- `Ctrl+1` switches to Chat
- `Ctrl+2` switches to Workspace

## Direct policy-gated WSL commands

The Textual frontend supports structured `/sys` invocations. Commands are sent
as an executable plus argument list; the frontend does not introduce a free
shell-string path.

```text
/sys mkdir -p /home/liara/workspace/translator_worker
/sys ls -la /home/liara/workspace
```

For a controlled file write, enter the `tee` command, use `Shift+Enter`, then
enter the file content. A normal `Enter` submits the complete request:

```text
/sys tee /home/liara/workspace/translator_worker/worker.py
print("hello from LIARA")
```

The one-line form is also available for short content:

```text
/sys tee /home/liara/workspace/probe.py --stdin "print('probe')"
```

`mkdir`, `touch` and `tee` are only reported as successful when the WSL
executor reads the target back and returns `mutation_verified=true`. File
writes additionally return size and SHA-256 evidence. After a verified write,
the active Workspace details view is refreshed.

Normal chat requests now include `sandbox_root`. Model statements such as
"files were created" are visibly marked as unverified unless the final API
payload contains matching verified write evidence. A single verified target
does not validate additional paths merely mentioned by the model.

## SSE Assumptions

`textual_chat/client.py` follows the current SSE contract from `POST /chat/stream`:

- reads `event:` and `data:` lines
- consumes `chunk` text parts
- captures `final` payload metadata
- stops on `done`

Ignored events (`progress`, `heartbeat`) are tolerated and do not break streaming.

## Runtime dependencies

- textual
- rich
- httpx

Example install:

```powershell
python -m pip install textual rich httpx
```
