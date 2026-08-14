"""Unit tests for the legacy audit/governance migration script's helper functions."""

from scripts.migrate_legacy_audit_data import (
    _deterministic_legacy_id,
    _sanitize_quarantine_preview,
)


def test_deterministic_legacy_id_is_stable_across_calls():
    line = '{"proposal_id": "sys-prop-abc"}'
    assert _deterministic_legacy_id("evt", line) == _deterministic_legacy_id("evt", line)


def test_deterministic_legacy_id_differs_by_prefix_and_content():
    line_a = '{"a": 1}'
    line_b = '{"a": 2}'
    assert _deterministic_legacy_id("evt", line_a) != _deterministic_legacy_id("aud", line_a)
    assert _deterministic_legacy_id("evt", line_a) != _deterministic_legacy_id("evt", line_b)


class TestSanitizeQuarantinePreview:
    def test_redacts_colon_style_key_value(self):
        assert "abc123XYZ" not in _sanitize_quarantine_preview("auth_token: abc123XYZ")

    def test_redacts_json_style_key_value(self):
        redacted = _sanitize_quarantine_preview('{"apiKey": "sk-12345"}')
        assert "sk-12345" not in redacted
        assert "apiKey" in redacted  # key name itself is not sensitive

    def test_redacts_equals_style_key_value(self):
        assert "hunter2" not in _sanitize_quarantine_preview("password=hunter2")

    def test_redacts_bearer_scheme_token_without_leaking_it(self):
        redacted = _sanitize_quarantine_preview(
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def"
        )
        assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
        assert "Bearer" not in redacted or "[REDACTED]" in redacted

    def test_redacts_keyword_embedded_in_larger_identifier(self):
        # A strict \bauth\b word boundary would miss this -- the value must
        # still be redacted even though the key isn't the bare word "auth".
        assert "abc123XYZ" not in _sanitize_quarantine_preview("auth_token: abc123XYZ")
        assert "sk-999" not in _sanitize_quarantine_preview('userApiKey="sk-999"')

    def test_leaves_non_sensitive_text_unchanged(self):
        line = "this is not valid json"
        assert _sanitize_quarantine_preview(line) == line

    def test_truncates_long_lines(self):
        long_line = "x" * 1000
        redacted = _sanitize_quarantine_preview(long_line)
        assert len(redacted) < len(long_line)
        assert redacted.endswith("...[TRUNCATED]")
