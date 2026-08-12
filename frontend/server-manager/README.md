## LIARA Server Manager

Native desktop GUI in C + GTK4 for local LIARA service operations.

Detailed documentation:
- `docs/SERVER_MANAGEMENT_GUI.md`

Current scope:
- Start / Stop / Restart managed services
- Health polling
- Live process log view
- Packaged runtime folder with DLLs and config

### Build

```powershell
cd frontend/server-manager
meson setup builddir
meson compile -C builddir

or

C:\msys64\usr\bin\bash.exe -lc 'export PATH=/ucrt64/bin:$PATH; cd /c/ai/LIARA/frontend/server-manager && meson compile -C builddir'
```

Run:

```powershell
frontend/server-manager/builddir/liara-server-manager.exe
```

### Package

```powershell
cd frontend/server-manager
.\package.ps1
```

This creates:
- `dist/bin/liara-server-manager.exe`
- `dist/bin/gdbus.exe`
- `dist/bin/gspawn-win64-helper.exe`
- `dist/bin/gspawn-win64-helper-console.exe`
- `dist/lib/*.dll`
- `dist/config/server-manager.json`
- `dist/config/glib-2.0/schemas`
- `dist/cache/`
- `dist/logs/ui/`
- `dist/run-liara-server-manager.exe` (preferred launcher if Rust/cargo is available)
- `dist/run-liara-server-manager.cmd`

Launcher version check:

```powershell
dist/run-liara-server-manager.exe --version
```

On every launcher start, a stamp line is appended to:
- `dist/logs/ui/server-manager.log`

### Config

`dist/config/server-manager.json`

```json
{
  "autostart": false,
  "restart_on_nonzero": false,
  "start_delay_ms": 1500,
  "env_file": "C:\\ai\\LIARA\\.env",
  "log_level": "INFO"
}
```

`autostart` is intentionally `false` by default.

`env_file` is loaded by the server manager before it spawns Python services.
This is in addition to any internal Python-side dotenv loading.

`log_level` controls UI and file log verbosity. Supported values:
- `DEBUG`
- `INFO`
- `WARN`
- `ERROR`
