from __future__ import annotations

from services.speech import SpeechPlanner


def test_planner_assigns_semantic_roles_and_prosody():
    plan = SpeechPlanner().plan(
        "- Erster Punkt.\n"
        "> Ein ruhiges Zitat.\n"
        "Ist alles bereit?\n\n"
        "Ein neuer Absatz."
    )

    assert [segment.semantic_role for segment in plan.segments] == [
        "list_item",
        "quote",
        "sentence",
        "sentence",
    ]
    assert plan.segments[0].prosody.emphasis == "moderate"
    assert plan.segments[0].pause_after_ms == 220
    assert plan.segments[1].prosody.pace == "measured"
    assert plan.segments[1].prosody.tone == "quoted"
    assert plan.segments[1].pause_after_ms == 260
    assert plan.segments[2].prosody.tone == "inquisitive"
    assert plan.segments[2].pause_after_ms == 320
    assert plan.segments[3].pause_after_ms == 320


def test_planner_normalizes_markup_and_tool_markers():
    plan = SpeechPlanner().plan(
        "**Annahmen** [TOOL:sys] Liara antwortet ruhig ."
    )

    assert [segment.text for segment in plan.segments] == ["Annahmen Liara antwortet ruhig."]


def test_planner_uses_short_pause_only_for_technical_splits():
    planner = SpeechPlanner(max_chars=24)
    plan = planner.plan(
        "Dieser lange Satz muss an Wortgrenzen in mehrere Teile zerlegt werden."
    )

    assert len(plan.segments) > 1
    assert all(len(segment.text) <= 24 for segment in plan.segments)
    assert all(segment.pause_after_ms == 80 for segment in plan.segments[:-1])
    assert plan.segments[-1].pause_after_ms == 320