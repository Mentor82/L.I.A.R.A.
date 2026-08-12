"""Unit tests for services/orchestrator/sys_selector.py

Covers:
- needs_sys() → True/False
- select_sys_command() → correct command, category, context, intent
"""

import pytest

from services.orchestrator.sys_selector import (
    CommandCategory,
    COMMAND_CATEGORIES,
    needs_sys,
    select_sys_command,
)


# ── needs_sys ────────────────────────────────────────────────────────────────

class TestNeedsSys:
    def test_health_intent_en(self):
        assert needs_sys("Please run a system status health check")

    def test_health_intent_de(self):
        assert needs_sys("Bitte mache einen health check mit sys health")

    def test_time_query_en(self):
        assert needs_sys("What time is it now?")

    def test_time_query_de(self):
        assert needs_sys("Wie spät ist es heute?")

    def test_math_expression(self):
        assert needs_sys("berechne 7 * 8")

    def test_math_infix(self):
        assert needs_sys("3 * 9 + 2")

    def test_url_fetch(self):
        assert needs_sys("fetch https://example.com please")

    def test_workspace_list(self):
        assert needs_sys("list files in workspace")

    def test_web_lookup(self):
        assert needs_sys("What is Python?")

    def test_orientation_not_needs_sys(self):
        # Pure orientation — no web/sys intent
        assert not needs_sys("wer bist du und was kannst du")

    def test_plain_chat_not_needs_sys(self):
        assert not needs_sys("Danke für deine Hilfe!")


# ── select_sys_command ───────────────────────────────────────────────────────

class TestSelectSysCommand:
    def test_health_intent_maps_to_health_command(self):
        sel = select_sys_command("wie geht es dir heute?")
        assert sel.command == "health"
        assert sel.intent == "health"
        assert sel.category == CommandCategory.READ_INSPECT
        assert sel.context == "agent_health_check"

    def test_explicit_sys_health_maps_to_health_command(self):
        sel = select_sys_command("nutze sys health")
        assert sel.command == "health"
        assert sel.intent == "health"

    def test_current_self_status_maps_to_health_not_web(self):
        sel = select_sys_command("Wie ist dein aktueller Status?")
        assert sel.command == "health"
        assert sel.intent == "health"
        assert sel.context == "agent_health_check"

    def test_contextual_orchestrator_status_maps_to_health_not_web(self):
        query = "[Context: Focused on Gene Node ORCHESTRATOR] Wie ist dein aktueller Status?"
        assert needs_sys(query)
        sel = select_sys_command(query)
        assert sel.command == "health"
        assert sel.intent == "health"
        assert sel.context == "agent_health_check"

    def test_context_prefix_is_not_in_web_search_query(self):
        query = "[Context: Focused on Gene Node ORCHESTRATOR] What is machine learning?"
        sel = select_sys_command(query)
        assert sel.command == "curl"
        assert sel.intent == "web"
        assert sel.extra["search_query"] == "What+is+machine+learning"

    def test_python_compute(self):
        sel = select_sys_command("berechne 42 * 7")
        assert sel.command == "julia"
        assert sel.intent == "julia"
        assert sel.category == CommandCategory.COMPUTE_TRANSFORM
        assert sel.context == "agent_julia_exec"

    def test_math_infix_compute(self):
        sel = select_sys_command("3 + 4 * 2")
        assert sel.command == "julia"
        assert sel.category == CommandCategory.COMPUTE_TRANSFORM

    def test_url_fetch(self):
        sel = select_sys_command("open https://example.com")
        assert sel.command == "curl"
        assert sel.intent == "url_fetch"
        assert sel.category == CommandCategory.FETCH
        assert "https://example.com" in sel.args

    def test_url_fetch_strips_trailing_prose_punctuation(self):
        sel = select_sys_command("Rufe https://api.example.com/cards/118/de. auf")
        assert sel.args[-1] == "https://api.example.com/cards/118/de"

        wrapped = select_sys_command("Rufe (https://api.example.com/cards/118/de). auf")
        assert wrapped.args[-1] == "https://api.example.com/cards/118/de"

    def test_workspace_list(self):
        sel = select_sys_command("zeige dateien im workspace")
        assert sel.command == "find"
        assert sel.intent == "workspace"
        assert sel.category == CommandCategory.READ_INSPECT
        assert "/home/liara/workspace" in sel.args

    def test_workspace_read(self):
        sel = select_sys_command("read file README.md in workspace")
        assert sel.command == "cat"
        assert sel.intent == "workspace"
        assert sel.category == CommandCategory.READ_INSPECT

    def test_workspace_write_with_quoted_content(self):
        sel = select_sys_command('write "hello" to notes.txt')
        assert sel.command == "tee"
        assert sel.intent == "workspace_write"
        assert sel.category == CommandCategory.WRITE_MUTATE
        assert sel.stdin_text == "hello"
        assert "/home/liara/workspace/notes.txt" in sel.args

    def test_temp_file_creation(self):
        sel = select_sys_command("create empty temp file cache.txt")
        assert sel.command == "touch"
        assert sel.category == CommandCategory.WRITE_MUTATE
        assert "/home/liara/temp/cache.txt" in sel.args

    def test_temp_folder_creation(self):
        sel = select_sys_command("erstelle temp ordner reports")
        assert sel.command == "mkdir"
        assert sel.category == CommandCategory.WRITE_MUTATE
        assert "/home/liara/temp/reports" in sel.args

    def test_time_lookup(self):
        sel = select_sys_command("What time is it now?")
        assert sel.command == "date"
        assert sel.intent == "time"
        assert sel.category == CommandCategory.READ_INSPECT

    @pytest.mark.parametrize(
        "query",
        [
            "aktuelle UTC-Zeit",
            "ISO-8601 UTC",
            "current utc time",
            "Wie spaet ist es gerade in UTC?",
        ],
    )
    def test_time_lookup_utc_variants_use_date_with_utc_args(self, query):
        sel = select_sys_command(query)
        assert sel.command == "date"
        assert sel.intent == "time"
        assert sel.category == CommandCategory.READ_INSPECT
        assert sel.args == ["-u", "+%Y-%m-%dT%H:%M:%SZ"]

    def test_current_fact_query_prefers_web_over_time(self):
        sel = select_sys_command("Who is the current president of the USA?")
        assert sel.command == "curl"
        assert sel.intent == "web"
        assert sel.category == CommandCategory.FETCH

    def test_source_query_with_today_prefers_web_over_time(self):
        sel = select_sys_command(
            "Beantworte die Frage mit belastbaren Quellen von heute zum aktuellen Presidenten der USA."
        )
        assert sel.command == "curl"
        assert sel.intent == "web"
        assert sel.category == CommandCategory.FETCH

    def test_web_lookup(self):
        sel = select_sys_command("What is machine learning?")
        assert sel.command == "curl"
        assert sel.intent == "web"
        assert sel.category == CommandCategory.FETCH
        assert any("wikipedia" in a for a in sel.args)

    def test_compute_takes_priority_over_web(self):
        """'berechne' should select julia, not curl."""
        sel = select_sys_command("berechne was ist 100 celsius in fahrenheit")
        assert sel.command == "julia"

    def test_url_takes_priority_over_web(self):
        sel = select_sys_command("search https://openai.com for info")
        assert sel.command == "curl"
        assert sel.intent == "url_fetch"


# ── CommandCategory taxonomy ─────────────────────────────────────────────────

class TestCommandCategories:
    def test_read_inspect_commands(self):
        for cmd in ("cat", "ls", "grep", "head", "tail", "find", "date", "health"):
            assert COMMAND_CATEGORIES[cmd] == CommandCategory.READ_INSPECT

    def test_compute_commands(self):
        for cmd in ("python3", "python"):
            assert COMMAND_CATEGORIES[cmd] == CommandCategory.COMPUTE_TRANSFORM

    def test_julia_is_compute_command(self):
        assert COMMAND_CATEGORIES["julia"] == CommandCategory.COMPUTE_TRANSFORM

    def test_fetch_commands(self):
        assert COMMAND_CATEGORIES["curl"] == CommandCategory.FETCH

    def test_write_commands(self):
        for cmd in ("mkdir", "touch", "tee"):
            assert COMMAND_CATEGORIES[cmd] == CommandCategory.WRITE_MUTATE

    def test_dangerous_not_in_categories(self):
        for cmd in ("rm", "dd", "sudo", "mkfs", "chmod"):
            assert cmd not in COMMAND_CATEGORIES
