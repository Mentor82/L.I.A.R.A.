## LIARA GTK UI

Native desktop GUI in C + GTK4 for the local `liara-api`.

Current scope:
- `POST /chat`
- `POST /chat/stream`
- `GET /history`
- `GET /tools`
- `POST /tools/{tool_name}/invoke`
- `GET /health`
- `GET /health/backends`
- Built-in Explorer page over `/tools/sys/invoke` for `/home/liara` and `/home/liara/workspace`

Explorer notes:
- Uses guarded `/sys` command execution for directory listing (`ls -la`) and file preview (`cat`).
- Sensitive paths such as `/home/liara/.ssh` remain blocked by policy and are not readable.

The GUI is intentionally thin. Business logic stays in the existing API and
service layers.

### Chat UI Features

#### Modern Chat Bubbles (ListBox-based)
- **Clean bubble layout**: User messages right-aligned (blue), Assistant left-aligned (dark/cyan)
- **Proper spacing**: Spacers for responsive alignment without manual halign
- **Dark-theme optimized**: Cyan accents for LIARA, blue for user
- **Streaming support**: Update label text in real-time without redrawing entire bubble

See [CHAT_BUBBLES.md](CHAT_BUBBLES.md) for API and usage.

#### Developer Panel (Collapsible Metadata)
- **Details toggle**: Click "Details ▼" to expand/collapse metadata
- **Metadata display**: Key-value pairs (run_id, llm_model, ttft_ms, etc.)
- **Tool styling**: Special blue-bordered variant for tool execution
- **Smooth transitions**: 200ms slide-down animation
- **Selectable text**: Copy metadata values directly from the UI

See [DEV_PANEL.md](DEV_PANEL.md) for API and usage examples.

#### Integration Examples

See [CHAT_INTEGRATION_GUIDE.md](CHAT_INTEGRATION_GUIDE.md) for complete code examples on:
- Streaming long responses
- Adding metadata panels to messages
- Tool execution display
- Custom styling

### Build

Required native packages (install via MSYS2 mingw64):

```powershell
C:\msys64\usr\bin\pacman.exe -Sy --noconfirm `
  mingw-w64-x86_64-gtk4 `
  mingw-w64-x86_64-libsoup3 `
  mingw-w64-x86_64-json-glib `
  mingw-w64-x86_64-meson `
  mingw-w64-x86_64-ninja
```

Build commands — use the wrapper script, which sets the MSYS2 mingw64 PATH automatically:

```powershell
cd frontend/WMTool-Liara
.\build.ps1          # configure + compile
.\build.ps1 -Clean   # wipe builddir first, then configure + compile
```

Manual build (when debugging toolchain issues):

```powershell
cd frontend/WMTool-Liara
$env:PATH = "C:\msys64\mingw64\bin;" + $env:PATH
$env:PKG_CONFIG_PATH = "C:\msys64\mingw64\lib\pkgconfig"
meson setup builddir
meson compile -C builddir
```

The executable is intentionally thin (`src/main.c`) and links against shared
runtime modules:
- `liara-api` (HTTP/API client layer)
- `liara-window` (UI composition + page logic)

This yields dynamic libraries (`*.dll`) in `builddir` and keeps the app modular.

Run:

```powershell
frontend/WMTool-Liara/builddir/liara-gtk-ui.exe
```

The default API base URL is `http://127.0.0.1:8010`.

Direct API mode (no universal bridge client):

- WMTool-Liara talks directly to `liara-api` on port `8010` (`/chat`, `/chat/stream`, `/history`, `/tools`).
- Legacy OpenAI-bridge style endpoints (for example `http://127.0.0.1:8011/v1`) are auto-migrated to direct API base URL `http://127.0.0.1:8010` when loading/saving connection settings.

### Package Runtime Files

To create a self-contained local folder with required GTK DLLs and GLib schemas:

```powershell
cd frontend/WMTool-Liara
.\package.ps1
```

If `dist` is locked by another process, packaging automatically falls back to a timestamped output directory such as `dist-20260419-185204`.

This creates:
- `dist/bin/liara-gtk-ui.exe`
- `dist/bin/gdbus.exe`
- `dist/lib/*.dll` (project modules + GTK/GLib runtime dependencies)
- `dist/config/style.css`
- `dist/config/lserv.json`
- `dist/cache/`
- `dist/config/glib-2.0/schemas`
- `dist/logs/ui/`
- `dist/run-liara-gtk-ui.cmd`
- `dist/run-liara-gtk-ui-dev.cmd`

Use `dist/run-liara-gtk-ui.cmd` for the packaged launch so the GTK runtime and
GLib schema paths are set correctly.

Use `dist/run-liara-gtk-ui-dev.cmd` to start the client in dev mode. It reads
`LIARA_DEV_PASSWORD` when set and otherwise uses the packaged fallback password.

The server manager was extracted into its own package:
- `frontend/server-manager`
