# Debian WSL Hardening For LIARA

This document records the runtime hardening choices for the LIARA `/sys` sandbox after the migration from Alpine to Debian.

## Active Debian WSL Baseline

The Debian distro is used as the default LIARA WSL runtime.

Applied WSL configuration:

```ini
[boot]
systemd=true

[user]
default=liara

[interop]
appendWindowsPath=false

[automount]
enabled=false
```

## Why These Settings

`systemd=true`
- Keeps Debian behavior standard and compatible with service-style tooling when needed.

`default=liara`
- Ensures the runtime does not fall back to `root`.
- Matches LIARA's executor assumptions (`/home/liara/workspace`).

`appendWindowsPath=false`
- Prevents Windows binaries from silently appearing in the Linux PATH.
- Reduces accidental host tool execution from inside the sandbox.

`automount.enabled=false`
- Prevents automatic mounting of Windows drives like `/mnt/c`.
- Keeps `/sys` operations inside the Linux workspace boundary unless explicitly reconfigured.

## Runtime Expectations

- LIARA `/sys` commands run as user `liara`.
- Default working directory is `/home/liara/workspace`.
- Julia runtime is installed inside Debian via `juliaup`.
- The default LIARA runtime binary points to `/home/liara/.juliaup/bin/julia` so non-interactive WSL executions do not depend on shell PATH initialization.
- Python runtime is installed inside Debian via `python3`, `python3-venv`, and `python3-pip`.
- A workspace-local Python environment is available at `/home/liara/workspace/.venv`.
- Agentic dependency recovery uses only the typed `venv-pip install/show`
  profile. Package names must match `LIARA_AGENT_DEPENDENCY_ALLOWLIST`
  (default: `pydantic,pytest`); URLs, Git/VCS references, local paths and
  interactive installation stay blocked.
- Installation is recorded as a network-backed environment mutation targeting
  the workspace `.venv`, followed by a separate `pip show` verification.
- Workspace Python commands resolve to `.venv/bin/python`; LIARA does not
  mutate Debian's system Python or the Windows project environment.
- Host-installed Julia remains editor-facing only and should not be the runtime path used by LIARA.
- If direct Julia shell access is needed, it should run through LIARA `/sys` inside Debian WSL with the `julia` command policy, not via a host executable.
- The `/sys` Julia profile is intentionally narrow: workspace `.jl` scripts and `--version` are allowed; free-form `-e`/`--eval` execution stays blocked.
- The application-side Julia path is unified as well: `compute.run` and the internal simulation bridge stage allowlisted `.jl` models into WSL and execute them there.
- `JULIA_BRIDGE_MODE=wsl` is the application default. `local` is an explicit development override only and must not be inferred from a host-side `julia.exe`.

## Verification Commands

Check Debian runtime status:

```powershell
wsl -l -v
wsl -d Debian -- sh -lc "whoami; julia --version; pwd"
```

Check Python workspace environment:

```powershell
wsl -d Debian -- sh -lc ". /home/liara/workspace/.venv/bin/activate && python --version && pip --version"
```

Run the unified Julia smoke test:

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe scripts/julia_sys_smoke_test.py
```

Run the API smoke test for `POST /compute/run` (existing API instance):

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe scripts/compute_run_api_smoke_test.py
```

Run the API smoke test with temporary local server startup:

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe scripts/compute_run_api_smoke_test.py --with-server
```

The temporary server sets `LLAMA_CPP_MANAGED_BY_API=false`. This keeps the
compute smoke isolated: it must neither replace nor stop the inference process
owned by the running LIARA environment.

Check hardened assumptions:

```powershell
wsl -d Debian -- sh -lc "printf 'PATH=%s\n' \"$PATH\"; ls /mnt || true"
```

Expected outcomes:
- user is `liara`
- Julia is available
- Windows PATH is not appended
- `/mnt/c` is not auto-mounted

Note:
- On some newer WSL builds, `/mnt/c` may still exist as an empty directory placeholder even when Windows drive automount is disabled.
- The important check is that there is no active Windows drive mount exposed there and no host PATH injection.

## Operational Note

Changes to `/etc/wsl.conf` require a distro restart to take effect:

```powershell
wsl --terminate Debian
```

Then start Debian again with:

```powershell
wsl -d Debian
```
