"""Parser and lightweight executor mapping for #task YAML prompt specs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import yaml


_ALLOWED_TYPES = {
    "reasoning",
    "tool",
    "memory_reasoning",
    "graph_reasoning",
    "planning",
    "analysis",
}


@dataclass(frozen=True)
class TaskPromptSpec:
    task_type: str
    goal: str
    steps: list[str]
    output_fields: list[str]

    def explicit_tools(self) -> list[str]:
        names: list[str] = []
        for step in self.steps:
            if not step.lower().startswith("tool "):
                continue
            parts = step.split()
            if len(parts) < 2:
                continue
            candidate = parts[1].strip()
            if candidate and candidate not in names:
                names.append(candidate)
        return names

    def to_execution_query(self) -> str:
        lines = [self.goal.strip()]

        if self.steps:
            lines.append("\n[TASK_PIPELINE]")
            lines.extend(f"- {step}" for step in self.steps)

        if self.output_fields:
            fields = ", ".join(self.output_fields)
            lines.append("\n[TASK_OUTPUT_REQUIREMENTS]")
            lines.append(
                f"Answer must include these output fields explicitly: {fields}."
            )

        if any(step.lower() == "rds" for step in self.steps) or "rds" in {
            field.lower() for field in self.output_fields
        }:
            lines.append("Include a numeric rds field (0-100) at the end of the answer.")

        return "\n".join(lines).strip()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "type": self.task_type,
            "goal": self.goal,
            "steps": list(self.steps),
            "output": list(self.output_fields),
            "explicit_tools": self.explicit_tools(),
        }


def parse_task_prompt(query: str) -> TaskPromptSpec | None:
    text = (query or "").strip()
    if not text.startswith("#task"):
        return None

    # Accept either plain YAML with '#task' header or fenced markdown blocks.
    text = re.sub(r"^```(?:ya?ml)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if not text.startswith("#task"):
        return None

    yaml_text = text[len("#task") :].strip()
    if not yaml_text:
        raise ValueError("#task payload is empty")

    payload = yaml.safe_load(yaml_text)
    if not isinstance(payload, dict):
        raise ValueError("#task payload must be a mapping")

    task_type = str(payload.get("type") or "").strip().lower()
    goal = str(payload.get("goal") or "").strip()
    steps_raw = payload.get("steps") or []
    output_raw = payload.get("output") or []

    if task_type not in _ALLOWED_TYPES:
        raise ValueError(f"unsupported task type '{task_type}'")
    if not goal:
        raise ValueError("task goal is required")
    if not isinstance(steps_raw, list):
        raise ValueError("steps must be a list")
    if not isinstance(output_raw, list):
        raise ValueError("output must be a list")

    steps = [str(item).strip() for item in steps_raw if str(item).strip()]
    output_fields = [str(item).strip() for item in output_raw if str(item).strip()]

    if not steps:
        raise ValueError("steps must not be empty")

    return TaskPromptSpec(
        task_type=task_type,
        goal=goal,
        steps=steps,
        output_fields=output_fields,
    )