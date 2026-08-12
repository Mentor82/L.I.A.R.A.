"""Planner stage for orchestrator split.

Owns prompt construction from query and tool outputs.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

from services.contracts import PlannerPlan, PlannerRequest
import yaml

# Common German function words that are unambiguous enough as language signals.
# Checked as whole-word tokens to avoid false positives.
_GERMAN_STOPWORDS = frozenset(
    [
        "ich", "du", "er", "sie", "es", "wir", "ihr",
        "ist", "sind", "war", "wird", "werden", "habe", "haben", "hat",
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "eines",
        "und", "oder", "aber", "nicht", "kein", "keine",
        "was", "wie", "wann", "wo", "wer", "warum", "welche", "welcher", "welches",
        "bitte", "danke", "nein", "ja",
        "auf", "mit", "von", "bei", "nach", "aus", "über", "unter", "vor",
        "aktuell", "aktuelle", "aktuellen", "neu", "neue", "neuen",
        "kann", "kannst", "könnte", "koennte", "soll", "sollte", "muss", "müssen", "muessten",
        "gibt", "geben", "zeig", "zeige", "erkläre", "erklär", "erklaere", "erklaer",
        # ASCII-transcribed German (ue/ae/oe substitutions)
        "heisst", "weiss", "strasse", "moeglich", "moegliche", "koennen",
        "saetzen", "satz", "praesident", "fuer", "ueber", "waere",
        "erklaerung", "beschreibe", "beschreibung", "nochmal", "kurz", "bitte",
    ]
)

_GERMAN_CHARS = frozenset("äöüÄÖÜß")

# Regex-based signal for common ASCII transcriptions of German umlaut/Eszett words.
# This detects forms like "fuer", "ueber", "koennen", "heisst", "strasse".
_GERMAN_ASCII_WORD_PATTERN = re.compile(
    r"\b(?:"
    r"fuer|ueber|gegenueber|zurueck|"
    r"koennen|koennte|koennte|moeglich|moeglichkeit|"
    r"waere|haette|haetten|gaebe|"
    r"heisst|weiss|strasse|grosse|groesser|"
    r"erklaer|erklaere|erklaerung|"
    r"praesident|saetze|saetzen"
    r")\b",
    flags=re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    """Return 'German' or 'English' based on lightweight heuristics.

    Priority order:
    1. Explicit preference override ("bitte auf deutsch", "in english please")
    2. German-specific Unicode characters (ä/ö/ü/ß) — strong signal
    3. Regex-based ASCII German orthography (ae/oe/ue/ss words)
    4. German stopword density — moderate fallback signal
    """
    lower = text.lower()

    # --- explicit overrides ---
    if any(tok in lower for tok in ("bitte auf deutsch", "bitte in deutsch", "auf deutsch", "deutsch")):
        return "German"
    if any(tok in lower for tok in ("in english", "please in english", "respond in english", "answer in english")):
        return "English"

    # --- Unicode character signal ---
    german_char_count = sum(1 for ch in text if ch in _GERMAN_CHARS)
    if german_char_count >= 1:
        return "German"

    # --- ASCII German orthography signal ---
    if _GERMAN_ASCII_WORD_PATTERN.search(text):
        return "German"

    # --- stopword density fallback ---
    tokens = re.findall(r"[a-zäöüß]+", lower)
    if tokens:
        hits = sum(1 for t in tokens if t in _GERMAN_STOPWORDS)
        if hits / len(tokens) >= 0.15:  # ≥15 % German stopwords  →  German
            return "German"

    return "English"


class QueryPlanner:
    """Builds LLM input prompt from current execution context."""

    _DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "system_promt.yaml"

    GENERIC_STYLE_PHRASES = (
        "it seems",
        "based on the information you provided earlier",
        "according to the information you provided earlier",
        "how can i assist you further",
        "is there anything specific you would like",
    )

    _NUMERIC_ONLY_HINTS = (
        "gib nur die zahl",
        "nur die zahl",
        "nur zahl",
        "only the number",
        "just the number",
        "numbers only",
        "digits only",
    )

    def __init__(self) -> None:
        self._system_content_block = self._load_system_content_block()

    @property
    def system_context(self) -> str:
        """Return the canonical configured LIARA system contract.

        The orchestrator may use this read-only text as explicit evidence for
        LIARA self-description.  It is configuration context, not a permission
        grant and not a substitute for runtime/retrieval evidence.
        """
        return self._system_content_block

    def build_plan(self, request: PlannerRequest) -> PlannerPlan:
        """Construct LLM prompt with routing-aware context prioritization."""
        # Tool outputs with mandatory citations
        tool_section = ""
        for tool_name, output in request.tool_outputs.items():
            rendered_output = self._render_tool_output(output)
            tool_section += f"\n[TOOL: {tool_name}]\n{rendered_output[:900]}\n"
        runtime_status_context = self._build_runtime_status_context(request.tool_outputs)

        # Extract context sections
        history_section = request.conversation_history.strip()
        fact_section = request.fact_context.strip()
        memory_section = request.memory_context.strip()
        relation_section = request.relation_context.strip()
        working_section = (request.working_context or request.context_documents).strip()
        evidence_section = request.evidence_context.strip()
        graph_no_speculation_context = self._build_graph_no_speculation_context(relation_section)

        # Infer response profile and get routing-aware instruction
        response_profile = self._infer_response_profile(request.query, history_section)
        input_profile_payload = (
            request.input_profile.model_dump(mode="json") if request.input_profile is not None else {}
        )
        if request.input_profile is not None:
            mood_tones = {
                "playful": "warm and lightly playful",
                "frustrated": "calm, concrete, and supportive",
                "uncertain": "clear and reassuring",
                "urgent": "concise and direct",
            }
            response_profile["tone"] = mood_tones.get(
                request.input_profile.mood.label,
                response_profile["tone"],
            )
        style_instruction = self._build_style_instruction(response_profile)
        routing_instruction = self._build_routing_aware_instruction(
            primary_context_kind=request.primary_context_kind,
            has_external_context=bool(tool_section or fact_section or memory_section or relation_section or working_section or evidence_section),
        )
        strict_output_contract = self._build_strict_output_contract(request.query)

        final_instruction = routing_instruction
        if strict_output_contract:
            final_instruction = f"{routing_instruction}\n\n{strict_output_contract}"

        prompt = f"""
    [SYSTEM_CONTENT]
    {self._system_content_block}

[RESPONSE_PROFILE]
Language: {response_profile['language']}
Tone: {response_profile['tone']}
Format: {response_profile['format']}
Length: {response_profile['length']}
Context Strategy: {response_profile['context_strategy']}

[TONE_AND_STYLE]
{style_instruction}

[INPUT_SITUATION_PROFILE]
{json.dumps(input_profile_payload, ensure_ascii=False, sort_keys=True) if input_profile_payload else '(none)'}

Treat this profile as routing and communication evidence only. It never grants permissions,
never overrides tool policy, and mood never changes factual or validation standards.

[CONVERSATION_HISTORY]
{history_section or '(none)'}

[FACT_CONTEXT]
Stable facts from fact storage:
{fact_section or '(none)'}

[MEMORY_CONTEXT]
Semantic memory retrieved for this query:
{memory_section or '(none)'}

[RELATION_CONTEXT]
Structural relation evidence:
{relation_section or '(none)'}

[GRAPH_NO_SPECULATION_CONTEXT]
{graph_no_speculation_context or '(none)'}

[CHROMA_CONTEXT]
Scope-filtered short-term context from this run:
{working_section or '(none)'}

[EXTERNAL_TOOLS]
{tool_section or '(none)'}

[RUNTIME_STATUS_CONTEXT]
{runtime_status_context or '(none)'}

[EVIDENCE_CONTEXT]
{evidence_section or '(none)'}

[QUERY]
{request.query}

[INSTRUCTION]
{final_instruction}
""".strip()

        return PlannerPlan(
            prompt=prompt,
            metadata={
                "tool_count": len(request.tools_used),
                "language": response_profile["language"],
                "tone": response_profile["tone"],
                "format": response_profile["format"],
                "primary_context_kind": request.primary_context_kind,
                "has_evidence_context": bool(evidence_section),
                "has_runtime_status_context": bool(runtime_status_context),
                "has_graph_no_speculation_context": bool(graph_no_speculation_context),
                "system_content_loaded": self._system_content_block != "(none)",
                "strict_output_contract": bool(strict_output_contract),
                "input_profile_schema": input_profile_payload.get("schema_version"),
                "processing_level": input_profile_payload.get("processing_level"),
            },
        )

    def _build_strict_output_contract(self, query: str) -> str:
        """Return hard output contract for strict-format user requests.

        This is intentionally query-driven and deterministic to reinforce
        user-specified output constraints such as numeric-only responses.
        """
        normalized_query = (query or "").lower()
        if not normalized_query:
            return ""

        if any(hint in normalized_query for hint in self._NUMERIC_ONLY_HINTS):
            return (
                "STRICT_OUTPUT_MODE: numeric_only. "
                "Return exactly one numeric literal as the full answer. "
                "No leading or trailing text, no labels, no units, no markdown, "
                "no code fences, and no citations."
            )

        return ""

    @classmethod
    def _resolve_system_prompt_path(cls) -> Path:
        configured = (os.getenv("LIARA_SYSTEM_PROMPT_PATH") or "").strip()
        if configured:
            return Path(configured)
        return cls._DEFAULT_SYSTEM_PROMPT_PATH

    @classmethod
    def _load_system_content_block(cls) -> str:
        """Load and compact core system content from YAML config.

        This keeps the final prompt deterministic while avoiding a full raw YAML dump.
        """
        path = cls._resolve_system_prompt_path()
        if not path.exists():
            return "(none)"

        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return "(none)"

        if not isinstance(payload, dict):
            return "(none)"

        def _as_dict(value: object) -> dict:
            return value if isinstance(value, dict) else {}

        def _as_list(value: object) -> list:
            return value if isinstance(value, list) else []

        root = _as_dict(payload.get("LIARA_SYSTEM"))
        if not root:
            return "(none)"

        identity = _as_dict(root.get("IDENTITY"))
        principles = _as_list(root.get("CORE_PRINCIPLES"))
        environment = _as_dict(root.get("ENVIRONMENT"))
        tools = _as_dict(root.get("TOOLS"))
        session_model = _as_dict(root.get("SESSION_MODEL"))
        context_rules = _as_dict(root.get("CONTEXT_RULES"))
        scout = _as_dict(root.get("SCOUT"))
        router = _as_dict(root.get("ROUTER"))
        judge = _as_dict(root.get("JUDGE"))
        validator = _as_dict(root.get("VALIDATOR"))
        memory_gate = _as_dict(root.get("MEMORY_GATE"))
        reasoning_control = _as_dict(root.get("REASONING_CONTROL"))
        reasoning_policy = _as_dict(root.get("REASONING_POLICY"))
        escalation = _as_dict(root.get("ESCALATION"))
        output_policy = _as_dict(root.get("OUTPUT_POLICY"))

        workspace = _as_dict(environment.get("WORKSPACE"))
        permissions = _as_dict(workspace.get("PERMISSIONS"))
        filesystem = _as_dict(workspace.get("FILESYSTEM"))
        execution = _as_dict(workspace.get("EXECUTION"))
        writable_paths = _as_list(filesystem.get("writable_paths"))
        restricted_paths = _as_list(filesystem.get("restricted_paths"))

        sys_tool = _as_dict(tools.get("SYS"))
        memory_tool = _as_dict(tools.get("MEMORY"))
        search_tool = _as_dict(tools.get("SEARCH"))
        sys_constraints = _as_list(sys_tool.get("constraints"))
        memory_types = _as_list(memory_tool.get("types"))
        search_constraints = _as_list(search_tool.get("constraints"))

        required_fields = _as_list(session_model.get("REQUIRED_FIELDS"))
        session_rules = _as_list(session_model.get("RULES"))

        context_structure = _as_list(context_rules.get("STRUCTURE"))
        context_principles = _as_list(context_rules.get("PRINCIPLES"))

        scout_methods = _as_list(scout.get("METHODS"))
        scout_rules = _as_list(scout.get("RULES"))

        router_rules = _as_list(router.get("RULES"))

        judge_checks = _as_list(judge.get("CHECKS"))
        judge_rules = _as_list(judge.get("RULES"))

        validator_checks = _as_list(validator.get("CHECKS"))
        validator_rules = _as_list(validator.get("RULES"))

        memory_gate_rules = _as_list(memory_gate.get("RULES"))

        reasoning_rules = _as_list(reasoning_control.get("RULES"))

        thinking_policy = _as_dict(reasoning_policy.get("THINKING"))
        reasoning_public_policy = _as_dict(reasoning_policy.get("REASONING"))
        thinking_includes = _as_list(thinking_policy.get("includes"))
        reasoning_includes = _as_list(reasoning_public_policy.get("includes"))
        reasoning_constraints = _as_list(reasoning_public_policy.get("constraints"))

        # New sections (2026-07-13)
        self_validation = _as_dict(root.get("SELF_VALIDATION"))
        governance = _as_dict(root.get("GOVERNANCE"))
        memory_lifecycle = _as_dict(root.get("MEMORY_LIFECYCLE"))
        async_execution = _as_dict(root.get("ASYNC_EXECUTION"))
        audit_trail = _as_dict(root.get("AUDIT_TRAIL"))
        capabilities_summary = _as_dict(root.get("CAPABILITIES_SUMMARY"))

        escalation_strategy = _as_list(escalation.get("STRATEGY"))
        escalation_purpose = _as_list(escalation.get("PURPOSE"))

        output_requirements = _as_list(output_policy.get("REQUIREMENTS"))
        output_avoid = _as_list(output_policy.get("AVOID"))
        generation_phase = _as_list(output_policy.get("GENERATION_PHASE"))

        lines: list[str] = []
        name = str(identity.get("name") or "Liara")
        role = str(identity.get("role") or "AI-Orchestrator")
        description = str(identity.get("description") or "").strip()
        lines.append(f"Identity: {name} ({role})")
        if description:
            lines.append(f"Description: {description}")

        if principles:
            lines.append("Core Principles:")
            lines.extend(f"- {item}" for item in principles)

        workspace_type = str(workspace.get("type") or "").strip()
        workspace_access = str(workspace.get("access") or "").strip()
        workspace_description = str(workspace.get("description") or "").strip()
        if workspace_type or workspace_access or workspace_description:
            lines.append("Environment Workspace:")
            if workspace_type:
                lines.append(f"- type: {workspace_type}")
            if workspace_access:
                lines.append(f"- access: {workspace_access}")
            if workspace_description:
                lines.append(f"- description: {workspace_description}")

        if permissions:
            lines.append("Environment Permissions:")
            for key, value in permissions.items():
                lines.append(f"- {key}: {value}")

        if writable_paths:
            lines.append("Environment Writable Paths:")
            lines.extend(f"- {item}" for item in writable_paths)

        if restricted_paths:
            lines.append("Environment Restricted Paths:")
            lines.extend(f"- {item}" for item in restricted_paths)

        if execution:
            lines.append("Environment Execution:")
            for key, value in execution.items():
                lines.append(f"- {key}: {value}")

        sys_description = str(sys_tool.get("description") or "").strip()
        memory_description = str(memory_tool.get("description") or "").strip()
        search_description = str(search_tool.get("description") or "").strip()
        if sys_description or memory_description or search_description:
            lines.append("Tools:")
            if sys_description:
                lines.append(f"- SYS: {sys_description}")
            if memory_description:
                lines.append(f"- MEMORY: {memory_description}")
            if search_description:
                lines.append(f"- SEARCH: {search_description}")

        if sys_constraints:
            lines.append("Tools SYS Constraints:")
            lines.extend(f"- {item}" for item in sys_constraints)

        if memory_types:
            lines.append("Tools MEMORY Types:")
            lines.extend(f"- {item}" for item in memory_types)

        if search_constraints:
            lines.append("Tools SEARCH Constraints:")
            lines.extend(f"- {item}" for item in search_constraints)

        if required_fields:
            lines.append("Session Model Required Fields:")
            lines.extend(f"- {item}" for item in required_fields)

        if session_rules:
            lines.append("Session Model Rules:")
            lines.extend(f"- {item}" for item in session_rules)

        if context_structure:
            lines.append("Context Structure:")
            lines.extend(f"- {item}" for item in context_structure)

        if context_principles:
            lines.append("Context Principles:")
            lines.extend(f"- {item}" for item in context_principles)

        scout_purpose = str(scout.get("PURPOSE") or "").strip()
        scout_scoring_model = str(scout.get("SCORING_MODEL") or "").strip()
        if scout_purpose:
            lines.append(f"Scout Purpose: {scout_purpose}")
        if scout_methods:
            lines.append("Scout Methods:")
            lines.extend(f"- {item}" for item in scout_methods)
        if scout_scoring_model:
            lines.append(f"Scout Scoring Model: {scout_scoring_model}")
        if scout_rules:
            lines.append("Scout Rules:")
            lines.extend(f"- {item}" for item in scout_rules)

        router_purpose = str(router.get("PURPOSE") or "").strip()
        router_decision_model = str(router.get("DECISION_MODEL") or "").strip()
        router_selection = str(router.get("SELECTION") or "").strip()
        if router_purpose:
            lines.append(f"Router Purpose: {router_purpose}")
        if router_decision_model:
            lines.append(f"Router Decision Model: {router_decision_model}")
        if router_selection:
            lines.append(f"Router Selection: {router_selection}")
        if router_rules:
            lines.append("Router Rules:")
            lines.extend(f"- {item}" for item in router_rules)

        judge_purpose = str(judge.get("PURPOSE") or "").strip()
        if judge_purpose:
            lines.append(f"Judge Purpose: {judge_purpose}")
        if judge_checks:
            lines.append("Judge Checks:")
            lines.extend(f"- {item}" for item in judge_checks)
        if judge_rules:
            lines.append("Judge Rules:")
            lines.extend(f"- {item}" for item in judge_rules)

        validator_purpose = str(validator.get("PURPOSE") or "").strip()
        if validator_purpose:
            lines.append(f"Validator Purpose: {validator_purpose}")
        if validator_checks:
            lines.append("Validator Checks:")
            lines.extend(f"- {item}" for item in validator_checks)
        if validator_rules:
            lines.append("Validator Rules:")
            lines.extend(f"- {item}" for item in validator_rules)

        memory_gate_purpose = str(memory_gate.get("PURPOSE") or "").strip()
        if memory_gate_purpose:
            lines.append(f"Memory Gate Purpose: {memory_gate_purpose}")
        if memory_gate_rules:
            lines.append("Memory Gate Rules:")
            lines.extend(f"- {item}" for item in memory_gate_rules)

        reasoning_cost_model = str(reasoning_control.get("COST_MODEL") or "").strip()
        reasoning_utility_model = str(reasoning_control.get("UTILITY_MODEL") or "").strip()
        reasoning_entropy = str(reasoning_control.get("ENTROPY") or "").strip()
        reasoning_confidence_adjusted = str(reasoning_control.get("CONFIDENCE_ADJUSTED") or "").strip()
        if reasoning_cost_model:
            lines.append(f"Reasoning Cost Model: {reasoning_cost_model}")
        if reasoning_utility_model:
            lines.append(f"Reasoning Utility Model: {reasoning_utility_model}")
        if reasoning_entropy:
            lines.append(f"Reasoning Entropy: {reasoning_entropy}")
        if reasoning_confidence_adjusted:
            lines.append(f"Reasoning Confidence Adjusted: {reasoning_confidence_adjusted}")
        if reasoning_rules:
            lines.append("Reasoning Rules:")
            lines.extend(f"- {item}" for item in reasoning_rules)

        thinking_definition = str(thinking_policy.get("definition") or "").strip()
        thinking_disclosure = str(thinking_policy.get("disclosure") or "").strip()
        if thinking_definition or thinking_disclosure or thinking_includes:
            lines.append("Reasoning Policy Thinking:")
            if thinking_definition:
                lines.append(f"- definition: {thinking_definition}")
            if thinking_includes:
                lines.append("- includes:")
                lines.extend(f"  - {item}" for item in thinking_includes)
            if thinking_disclosure:
                lines.append(f"- disclosure: {thinking_disclosure}")

        reasoning_definition = str(reasoning_public_policy.get("definition") or "").strip()
        reasoning_disclosure = str(reasoning_public_policy.get("disclosure") or "").strip()
        if reasoning_definition or reasoning_disclosure or reasoning_includes or reasoning_constraints:
            lines.append("Reasoning Policy Visible Reasoning:")
            if reasoning_definition:
                lines.append(f"- definition: {reasoning_definition}")
            if reasoning_includes:
                lines.append("- includes:")
                lines.extend(f"  - {item}" for item in reasoning_includes)
            if reasoning_constraints:
                lines.append("- constraints:")
                lines.extend(f"  - {item}" for item in reasoning_constraints)
            if reasoning_disclosure:
                lines.append(f"- disclosure: {reasoning_disclosure}")

        # Render SELF_VALIDATION section (2026-07-13)
        if self_validation:
            lines.append("Self Validation:")
            validator_service = _as_dict(self_validation.get("VALIDATOR_SERVICE"))
            if validator_service:
                lines.append("- Validator Service:")
                lines.append(f"  - url: {validator_service.get('url', 'http://127.0.0.1:9090')}")
                endpoints = _as_list(validator_service.get("rest_endpoints"))
                if endpoints:
                    lines.append("  - endpoints:")
                    lines.extend(f"    - {e}" for e in endpoints)
                modes = _as_dict(validator_service.get("execution_modes"))
                if modes:
                    lines.append("  - execution_modes:")
                    for mode_name, mode_info in modes.items():
                        lines.append(f"    - {mode_name}: {_as_dict(mode_info).get('description', '')}")
            scopes = _as_list(self_validation.get("VALIDATION_SCOPES"))
            if scopes:
                lines.append("- Validation Scopes:")
                lines.extend(f"  - {scope}" for scope in scopes)
            val_rules = _as_list(self_validation.get("RULES"))
            if val_rules:
                lines.append("- Validator Rules:")
                lines.extend(f"  - {rule}" for rule in val_rules)

        # Render GOVERNANCE section (2026-07-13)
        if governance:
            lines.append("Governance:")
            flow = _as_dict(governance.get("PROPOSAL_DECISION_FLOW"))
            if flow:
                lines.append("- Proposal Decision Flow:")
                for step, desc in flow.items():
                    lines.append(f"  - {step}: {desc}")
            blocked = _as_dict(governance.get("BLOCKED_TOKENS"))
            if blocked:
                lines.append("- Blocked Tokens:")
                for token_type, tokens in blocked.items():
                    lines.append(f"  - {token_type}: {', '.join(tokens)}")
            enforcement = _as_list(governance.get("ENFORCEMENT"))
            if enforcement:
                lines.append("- Enforcement:")
                lines.extend(f"  - {rule}" for rule in enforcement)
            gov_rules = _as_list(governance.get("RULES"))
            if gov_rules:
                lines.append("- Governance Rules:")
                lines.extend(f"  - {rule}" for rule in gov_rules)

        # Render MEMORY_LIFECYCLE section (2026-07-13)
        if memory_lifecycle:
            lines.append("Memory Lifecycle:")
            states = _as_dict(memory_lifecycle.get("FACT_STATES"))
            if states:
                lines.append("- Fact States:")
                for state_name, state_info in states.items():
                    desc = _as_dict(state_info).get("description", "")
                    lines.append(f"  - {state_name}: {desc}")
            mem_rules = _as_list(memory_lifecycle.get("RULES"))
            if mem_rules:
                lines.append("- Memory Lifecycle Rules:")
                lines.extend(f"  - {rule}" for rule in mem_rules)

        # Render ASYNC_EXECUTION section (2026-07-13)
        if async_execution:
            lines.append("Async Execution:")
            async_rules = _as_list(async_execution.get("BEST_PRACTICES"))
            if async_rules:
                lines.append("- Best Practices:")
                lines.extend(f"  - {rule}" for rule in async_rules)

        # Render AUDIT_TRAIL section (2026-07-13)
        if audit_trail:
            lines.append("Audit Trail:")
            audit_design = _as_dict(audit_trail.get("APPEND_ONLY_DESIGN"))
            if audit_design:
                lines.append("- Append-Only Design:")
                paths = _as_list(audit_design.get("paths"))
                if paths:
                    lines.append("  - paths:")
                    lines.extend(f"    - {p}" for p in paths)
            audit_rules = _as_list(audit_trail.get("RULES"))
            if audit_rules:
                lines.append("- Audit Rules:")
                lines.extend(f"  - {rule}" for rule in audit_rules)

        # Render CAPABILITIES_SUMMARY section (2026-07-13)
        if capabilities_summary:
            lines.append("Capabilities Summary:")
            is_now = _as_dict(capabilities_summary.get("LIARA_IS_NOW"))
            if is_now:
                for capability_type, items in is_now.items():
                    items_list = _as_list(items)
                    lines.append(f"- {capability_type}:")
                    lines.extend(f"  - {item}" for item in items_list)

        if escalation_strategy:
            lines.append("Escalation Strategy:")
            lines.extend(f"- {item}" for item in escalation_strategy)
        if escalation_purpose:
            lines.append("Escalation Purpose:")
            lines.extend(f"- {item}" for item in escalation_purpose)

        if generation_phase:
            lines.append("Response Generation Phase:")
            lines.extend(f"- {item}" for item in generation_phase)

        if output_requirements:
            lines.append("Output Requirements:")
            lines.extend(f"- {item}" for item in output_requirements)

        if output_avoid:
            lines.append("Output Avoid:")
            lines.extend(f"- {item}" for item in output_avoid)

        rendered = "\n".join(line for line in lines if line)
        return rendered or "(none)"

    @staticmethod
    def _render_tool_output(output: object) -> str:
        """Render tool output as string, preferring structured summary."""
        if isinstance(output, dict):
            summary_text = str(output.get("summary_text") or "").strip()
            if summary_text:
                return summary_text
            try:
                return json.dumps(output, ensure_ascii=False)
            except TypeError:
                return str(output)
        return str(output)

    @classmethod
    def _build_runtime_status_context(cls, tool_outputs: dict[str, object]) -> str:
        """Return interpretation rules when sys health supplied runtime evidence."""
        sys_output = tool_outputs.get("sys") if isinstance(tool_outputs, dict) else None
        payload: object = sys_output
        if isinstance(sys_output, str):
            try:
                payload = json.loads(sys_output)
            except json.JSONDecodeError:
                payload = None

        if not isinstance(payload, dict):
            return ""

        has_runtime_snapshot = any(
            key in payload
            for key in (
                "api_health",
                "backend_health",
                "memory_backends",
                "embedding_runtime",
                "heartbeat",
            )
        )
        if not has_runtime_snapshot:
            return ""

        return (
            "Runtime Status Interpretation:\n"
            "- Interpret the LIARA runtime snapshot from tool evidence. The tool data is evidence, not a hint.\n"
            "- Use no external search for runtime status unless the user explicitly asks for external information.\n"
            "- Distinguish configured, reachable, healthy, degraded, unavailable, constrained, stale, and unknown.\n"
            "- api_health.status == \"ok\" means the API process responded successfully.\n"
            "- api_health.backends_configured only means a backend is configured; it does not prove that backend is healthy.\n"
            "- backend_health values are authoritative for backend availability when present.\n"
            "- memory_backends.status.degraded indicates that memory is reachable but operating with limitations.\n"
            "- embedding_runtime describes the active embedding execution path; report device, runtime_backend, dimensions, and model when relevant.\n"
            "- heartbeat.service_health describes the heartbeat service and collector state.\n"
            "- heartbeat.state and heartbeat.trend describe the current resource condition; heartbeat.confidence describes evidence confidence.\n"
            "- heartbeat.envelope.capacity below 0.25 means constrained capacity, not necessarily a failure.\n"
            "- max_parallel_jobs == 1 means the system is operational but should avoid claiming high parallel capacity.\n"
            "- Overall health can only be as strong as the weakest relevant evidence.\n"
            "- If a field or probe is missing, say unknown for that part instead of inferring it.\n"
            "- If probe_status contains errors, report them as runtime evidence gaps.\n"
            "- Keep the user-facing answer concise and in the user's language."
        )

    @staticmethod
    def _build_graph_no_speculation_context(relation_context: str) -> str:
        if "[relation]" not in (relation_context or "").lower():
            return ""
        return (
            "Graph No-Speculation Runtime Rule:\n"
            "- Direct graph relations in [RELATION_CONTEXT] are authoritative structural evidence for this answer.\n"
            "- Do not silently replace a graph relation target with a retrieval hit, memory fragment, or model assumption.\n"
            "- If other evidence appears to conflict with a graph relation, report the conflict explicitly instead of resolving it by speculation.\n"
            "- If the graph relation is relevant to the user's question, preserve its source, relation type, and target.\n"
            "- If the graph relation is not relevant, you may ignore it, but you must not contradict it.\n"
            "- If you are uncertain how to interpret a relation, say that the interpretation is uncertain and keep the relation intact."
        )

    def _infer_response_profile(self, query: str, history_section: str) -> dict[str, str]:
        """Infer language, tone, format, and length from query and history."""
        signal_text = f"{history_section}\n{query}"

        language = _detect_language(signal_text)

        response_format = "essay" if self._wants_essay(signal_text) else "structured prose"
        if self._wants_list(signal_text):
            response_format = "bullet list"

        length = "medium"
        if self._wants_long_form(signal_text):
            length = "long"
        elif self._wants_brief(signal_text):
            length = "short"

        tone = "natural, specific, non-generic"
        if self._wants_formal(signal_text):
            tone = "formal, precise, non-generic"

        context_strategy = (
            "Prioritize explicit user preferences from recent conversation history, then short-term context, then tool evidence."
        )

        return {
            "language": language,
            "tone": tone,
            "format": response_format,
            "length": length,
            "context_strategy": context_strategy,
        }

    def _build_style_instruction(self, profile: dict[str, str]) -> str:
        """Build style instruction based on inferred response profile."""
        banned = ", ".join(f'"{phrase}"' for phrase in self.GENERIC_STYLE_PHRASES)
        return (
            f"Write in {profile['language']}. "
            f"Use a {profile['tone']} voice. "
            f"Prefer {profile['format']} and target a {profile['length']} answer length. "
            "Be concrete and grounded in the user's request and prior turns. "
            "Avoid generic assistant filler, classroom-summary clichés, and repetitive meta-commentary. "
            f"Do not use phrases such as {banned} unless the user explicitly asks for that style. "
            "When prior conversation contains preferences such as language, style, or requested level of detail, carry them forward explicitly."
        )

    def _build_routing_aware_instruction(self, *, primary_context_kind: str, has_external_context: bool) -> str:
        """Build LLM instruction with routing-aware context prioritization.
        
        Routes instructions based on Librarian classification:
        - FACT_LOOKUP: Prioritize facts first
        - SESSION_RECALL: Prioritize history second
        - SEMANTIC_MEMORY: Prioritize memory/relations first
        - Otherwise: Balanced mode with all sources
        """
        base_citation = (
            "MANDATORY: Every factual claim from [TOOL], [FACT_CONTEXT], [MEMORY_CONTEXT], "
            "[RELATION_CONTEXT], or [CHROMA_CONTEXT] MUST be followed by [KNOWLEDGE_REFERENCE].\n"
            "Do NOT add [KNOWLEDGE_REFERENCE] for [CONVERSATION_HISTORY]. Never invent facts or citations.\n"
            "SELF-CHECK RULE: Before emitting any factual claim, verify it against available evidence in this order: "
            "[FACT_CONTEXT]/[TOOL] -> [MEMORY_CONTEXT]/[RELATION_CONTEXT] -> [CONVERSATION_HISTORY]. "
            "If evidence is missing, contradictory, or low-confidence, state uncertainty explicitly and ask for clarification "
            "instead of guessing. Do not use hardcoded world facts; rely on retrieved or provided evidence."
        )

        if primary_context_kind == "FACT_LOOKUP":
            return (
                f"FACT_LOOKUP MODE: Prioritize [FACT_CONTEXT] first (personal facts, system facts), "
                f"then [MEMORY_CONTEXT], then external sources.\n{base_citation}"
            )
        elif primary_context_kind == "SESSION_RECALL":
            return (
                f"SESSION_RECALL MODE: Prioritize [CONVERSATION_HISTORY] first, referencing specific prior turns. "
                f"Then use [FACT_CONTEXT], [MEMORY_CONTEXT].\n{base_citation}\nNever invent history."
            )
        elif primary_context_kind == "SEMANTIC_MEMORY":
            return (
                f"SEMANTIC_MEMORY MODE: Prioritize [MEMORY_CONTEXT] and [RELATION_CONTEXT] first for thematic connections, "
                f"then [FACT_CONTEXT], then external sources.\n{base_citation}"
            )
        elif not has_external_context:
            return (
                "Answer using conversation history accurately. "
                "Reference prior turns directly if user asks what was discussed. "
                "Do not invent sources."
            )
        else:
            return (
                f"Balanced mode: Use all context sources in order: "
                f"[CONVERSATION_HISTORY] → [FACT_CONTEXT] → [MEMORY_CONTEXT] → [RELATION_CONTEXT] → external.\n{base_citation}"
            )

    @staticmethod
    def _wants_essay(text: str) -> bool:
        """Check if user wants essay-style response."""
        t = text.lower()
        return any(token in t for token in ("aufsatz", "essay", "fließtext", "fliesstext"))

    @staticmethod
    def _wants_list(text: str) -> bool:
        """Check if user wants list-style response."""
        t = text.lower()
        return any(token in t for token in ("stichpunkte", "bullet", "liste", "auflisten"))

    @staticmethod
    def _wants_long_form(text: str) -> bool:
        """Check if user wants long-form response."""
        t = text.lower()
        return bool(re.search(r"\b(400|500|600|700|800|900|1000)\b", t)) or any(
            token in t for token in ("ausführlich", "ausfuehrlich", "detailliert", "lang", "ca 500", "ca. 500")
        )

    @staticmethod
    def _wants_brief(text: str) -> bool:
        """Check if user wants brief response."""
        t = text.lower()
        return any(token in t for token in ("kurz", "knapp", "in kurzform", "tl;dr", "tldr"))

    @staticmethod
    def _wants_formal(text: str) -> bool:
        """Check if user wants formal tone."""
        t = text.lower()
        return any(token in t for token in ("formal", "sachlich", "präzise", "praezise", "professionell"))
