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

The GUI is intentionally thin. Business logic stays in the existing API and
service layers.

### Build

Required native packages:
- GTK4 development files
- libsoup 3
- json-glib
- meson
- ninja

Build commands:

```powershell
cd frontend/gtk-ui
meson setup builddir
meson compile -C builddir
```

Run:

```powershell
frontend/gtk-ui/builddir/liara-gtk-ui.exe
```

The default API base URL is `http://127.0.0.1:8010`.

### Package Runtime Files

To create a self-contained local folder with required GTK DLLs and GLib schemas:

```powershell
cd frontend/gtk-ui
.\package.ps1
```

This creates:
- `dist/bin/liara-gtk-ui.exe`
- `dist/bin/gdbus.exe`
- `dist/lib/*.dll`
- `dist/config/style.css`
- `dist/cache/`
- `dist/config/glib-2.0/schemas`
- `dist/logs/ui/`
- `dist/run-liara-gtk-ui.cmd`

Use `dist/run-liara-gtk-ui.cmd` for the packaged launch so the GTK runtime and
GLib schema paths are set correctly.
