"""Shared visual theme for the Textual chat UI."""

APP_CSS = """
Screen {
    layout: vertical;
    background: #0b1020;
    color: #e6edf7;
}

Header {
    background: #d4a72c;
    color: #101826;
}

Footer {
    background: #111827;
    color: #d8e1ef;
}

#body {
    layout: horizontal;
    height: 1fr;
    padding: 0 1;
}

#sidebar {
    width: 33;
    min-width: 29;
    border: round #2b3c57;
    background: #0f172a;
    padding: 0 1;
}

#brand {
    border: round #d4a72c;
    padding: 0 1;
    margin-bottom: 1;
    background: #162033;
}

#status {
    border: round #2f80ed;
    padding: 0 1;
    margin-bottom: 1;
    background: #101c33;
}

#runtime {
    border: round #1c8c7d;
    padding: 0 1;
    margin-bottom: 1;
    background: #102625;
}

#cache {
    border: round #935f2d;
    padding: 0 1;
    margin-bottom: 1;
    background: #261b12;
}

#commands {
    border: round #334155;
    padding: 0 1;
    color: #cbd5e1;
    background: #0c1424;
}

#main {
    width: 1fr;
    padding-left: 1;
    layout: vertical;
}

#tab_bar {
    height: 3;
    margin-bottom: 1;
}

#tab_chat_btn,
#tab_workspace_btn {
    min-width: 14;
    margin-right: 1;
}

#chat_view,
#workspace_view {
    height: 1fr;
    layout: vertical;
}

.hidden {
    display: none;
}

#workspace_info {
    height: auto;
    border: round #2b3c57;
    padding: 0 1;
    margin-bottom: 1;
    background: #121d31;
    color: #eaf2ff;
}

#workspace_body {
    height: 1fr;
    layout: horizontal;
}

/* ── Left pane: folder tree only ───────────────────── */
#workspace_tree {
    width: 36;
    min-width: 26;
    border: round #334155;
    background: #0a1324;
    padding: 0 1;
    margin-right: 1;
}

/* ── Right pane: dir panel ──────────────────────────── */
#dir_panel {
    width: 1fr;
    layout: vertical;
}

#explorer_toolbar {
    height: 3;
    padding: 0 1;
    background: #0d1a2d;
    border: round #2b3c57;
    margin-bottom: 1;
    align: left middle;
}

#view_details_btn,
#view_tiles_btn {
    min-width: 12;
    margin-right: 1;
}

#path_label {
    width: 1fr;
    padding: 0 1;
    color: #64748b;
    text-style: italic;
}

/* Details view */
#details_table {
    height: 1fr;
    border: round #334155;
    background: #0a1324;
    color: #e6edf7;
}

#details_table > .datatable--header {
    background: #1b2a41;
    color: #f8fafc;
}

#details_table > .datatable--cursor {
    background: #16385d;
    color: #f8fafc;
}

/* Tiles / icons view */
#tiles_panel {
    height: 1fr;
    border: round #334155;
    background: #0a1324;
    padding: 1 2;
}

/* File preview pane (shown on file selection) */
#preview_pane {
    height: 35%;
    min-height: 8;
    border: round #1c3358;
    background: #080f1e;
    margin-top: 1;
    layout: vertical;
}

#preview_label {
    height: 1;
    padding: 0 1;
    background: #0d1e35;
    color: #94a3b8;
}

#file_viewer {
    height: 1fr;
    background: #080f1e;
    color: #e6edf7;
}

.hidden {
    display: none;
}

#activity {
    height: auto;
    border: round #2b3c57;
    padding: 0 1;
    margin-bottom: 1;
    background: #121d31;
    color: #eaf2ff;
}

#chat_log {
    height: 1fr;
    border: round #334155;
    background: #0a1324;
    padding: 0 1;
}

#composer {
    height: 8;
    max-height: 8;
    min-height: 8;
    margin-top: 1;
    border: round #2b3c57;
    background: #111c31;
    padding: 0 1;
    layout: horizontal;
    align-vertical: bottom;
}

#prompt {
    width: 1fr;
    margin-right: 1;
    height: 6;
    min-height: 6;
    max-height: 6;
    border: round #2b3c57;
    background: #0b1426;
    color: #e6edf7;
}

#send_btn {
    min-width: 10;
    height: 3;
    background: #2f80ed;
    color: #f8fbff;
}

#send_btn:hover {
    background: #2563cf;
}

#history_btn,
#mode_btn,
#cache_btn {
    min-width: 10;
    margin-right: 1;
    height: 3;
}

#history_btn {
    background: #165d86;
    color: #eff8ff;
}

#mode_btn {
    background: #1c8c7d;
    color: #effffb;
}

#cache_btn {
    background: #935f2d;
    color: #fff7ec;
}

.message-user {
    color: #9dc8ff;
}

.message-assistant {
    color: #7fe7cf;
}

.message-system {
    color: #f2bf63;
}
"""
