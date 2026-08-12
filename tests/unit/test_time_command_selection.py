"""Unit tests for date/time command selection in sys_selector."""
import pytest
from services.orchestrator.sys_selector import needs_sys, select_sys_command, CommandCategory


class TestTimeCommandSelection:
    """Test that time-related queries select the date command."""

    @pytest.mark.parametrize(
        "query",
        [
            "Was ist die aktuelle Zeit?",
            "Nenne mir die Uhrzeit",
            "Wie spät ist es jetzt?",
            "aktuelle Zeit", 
            "current time",
            "Welche Zeit haben wir?",
            "Sag mir die Zeit von jetzt",
        ],
    )
    def test_time_queries_need_sys(self, query: str):
        """Time queries should trigger sys tool need."""
        assert needs_sys(query), f"Query '{query}' should need sys"

    @pytest.mark.parametrize(
        "query",
        [
            "Was ist die aktuelle Zeit?",
            "Nenne mir die UTC Zeit",
            "Wie spät ist es?",
        ],
    )
    def test_time_queries_select_date_command(self, query: str):
        """Time queries should select date command."""
        selection = select_sys_command(query)
        assert selection.command == "date", f"Query '{query}' should select date command"
        assert selection.intent == "time"
        assert selection.context in {"agent_datetime_fetch", "agent_time_lookup"}
        assert selection.category == CommandCategory.READ_INSPECT
        if "UTC" in query:
            assert selection.args == ["-u", "+%Y-%m-%dT%H:%M:%SZ"]
        else:
            assert "+%Y-%m-%d %H:%M:%S %Z" in selection.args

    def test_date_format_iso_timezone(self):
        """Date command should use ISO format with timezone."""
        selection = select_sys_command("Wie lautet die aktuelle Uhrzeit?")
        assert selection.command == "date"
        assert len(selection.args) == 1
        assert "+%Y-%m-%d %H:%M:%S %Z" in selection.args[0]
