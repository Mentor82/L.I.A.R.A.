from services.shared.output_sanitizer import OutputSanitizer


def test_sanitizer_removes_internal_thinking_markers() -> None:
    sanitizer = OutputSanitizer()
    text = "Semantischer Gedanke: interner Schritt\nErgebnis: 84"

    result = sanitizer.sanitize(text)

    assert result.changed is True
    assert "Semantischer Gedanke" not in result.text
    assert "Ergebnis: 84" in result.text
    assert "internal_thinking_marker" in result.applied_rules


def test_sanitizer_removes_prompt_dump_markers() -> None:
    sanitizer = OutputSanitizer()
    text = "[SYSTEM_CONTENT]\nSecret\nAntwort: ok"

    result = sanitizer.sanitize(text)

    assert result.changed is True
    assert "[SYSTEM_CONTENT]" not in result.text
    assert "Antwort: ok" in result.text
    assert "prompt_leak_marker" in result.applied_rules


def test_sanitizer_fallback_when_everything_removed() -> None:
    sanitizer = OutputSanitizer()

    result = sanitizer.sanitize("[INSTRUCTION]\nchain-of-thought")

    assert result.changed is True
    assert result.text.startswith("The response was withheld")
    assert "fallback_safe_message" in result.applied_rules


def test_sanitizer_removes_context_marker_lines() -> None:
    sanitizer = OutputSanitizer()
    text = (
        "Faktische Informationen aus dem [FACT_CONTEXT] sind nicht notwendig.\n"
        "Die [CHROMA_CONTEXT]-Quelle wurde intern genutzt.\n"
        "Berechnungsergebnis: 81"
    )

    result = sanitizer.sanitize(text)

    assert result.changed is True
    assert "[FACT_CONTEXT]" not in result.text
    assert "[CHROMA_CONTEXT]" not in result.text
    assert "Berechnungsergebnis: 81" in result.text
    assert "prompt_leak_marker" in result.applied_rules


def test_sanitizer_removes_inline_tool_marker_but_keeps_text() -> None:
    sanitizer = OutputSanitizer()
    text = "Der Health Check ist erfolgreich. [TOOL]"

    result = sanitizer.sanitize(text)

    assert result.changed is True
    assert "[TOOL]" not in result.text
    assert result.text == "Der Health Check ist erfolgreich."
    assert "inline_source_marker" in result.applied_rules


def test_sanitizer_removes_inline_knowledge_reference_marker_but_keeps_text() -> None:
    sanitizer = OutputSanitizer()
    text = "Status ist ok. [KNOWLEDGE_REFERENCE]"

    result = sanitizer.sanitize(text)

    assert result.changed is True
    assert "[KNOWLEDGE_REFERENCE]" not in result.text
    assert result.text == "Status ist ok."
    assert "fallback_safe_message" not in result.applied_rules
    assert "inline_source_marker" in result.applied_rules


def test_sanitizer_removes_parameterized_sys_marker_but_keeps_sentence() -> None:
    sanitizer = OutputSanitizer()
    text = 'Die Suche ergab keinen Treffer. [SYS: curl -s "https://example.test"]'

    result = sanitizer.sanitize(text)

    assert result.text == "Die Suche ergab keinen Treffer."
    assert "SYS" not in result.text
    assert "inline_source_marker" in result.applied_rules


def test_sanitizer_removes_unicode_and_misspelled_reference_markers() -> None:
    sanitizer = OutputSanitizer()
    text = "Ergebnis eins.【KNOWLEDGE_REFERENCE】\nErgebnis zwei. [KNOWELDGE_REFERENCE: fake]"

    result = sanitizer.sanitize(text)

    assert result.text == "Ergebnis eins.\nErgebnis zwei."
    assert "REFERENCE" not in result.text
    assert "inline_source_marker" in result.applied_rules
