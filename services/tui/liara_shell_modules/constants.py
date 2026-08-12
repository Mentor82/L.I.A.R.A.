from __future__ import annotations

import os

from rich.console import Console

console = Console()

LIARA_DIR = os.path.join(os.path.expanduser("~"), ".liara")
HISTORY_FILE = os.path.join(LIARA_DIR, "shell_history")
SESSION_FILE = os.path.join(LIARA_DIR, "session.json")
MIN_MAX_TOKENS = 256
MAX_HISTORY_LIMIT = 500

SHELL_STYLE = {
    "completion-menu": "bg:#1f4a6d #ffffff",
    "completion-menu.completion": "bg:#1f4a6d #ffffff",
    "completion-menu.completion.current": "bg:#a6d5fa #000000",
}

KNOWN_COMMANDS = {
    "/help": None,
    "/status": None,
    "/show-config": None,
    "/health": None,
    "/history": {"20": None, "50": None, "100": None},
    "/context": None,
    "/session": {"show": None, "new": None, "set": None},
    "/mode": {"chat": None, "stream": None},
    "/provider": {"show": None, "set": None, "reset": None},
    "/model": {"show": None, "set": None, "reset": None},
    "/max-tokens": None,
    "/set": {"user": None, "timeout": None, "base-url": None},
    "/tools": None,
    "/sys": None,
    "/diag": None,
    "/paste": None,
    "/clear": None,
    "/cls": None,
    "/quit": None,
}
