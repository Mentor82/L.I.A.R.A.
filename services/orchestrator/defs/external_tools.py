import re
from typing import Any, Dict, List, Optional


def normalize_external_tool(tool: Any) -> Dict[str, Any]:
    if isinstance(tool, dict):
        return tool
    if hasattr(tool, "model_dump"):
        dumped = tool.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(tool, "dict"):
        try:
            dumped = tool.dict()
        except TypeError:
            dumped = tool.dict(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    if hasattr(tool, "function") or hasattr(tool, "type"):
        normalized = {
            "type": getattr(tool, "type", "function"),
            "function": getattr(tool, "function", {}) or {},
        }
        if isinstance(normalized.get("function"), dict):
            return normalized
    return {}


def extract_textual_tool_schema(tool: Any) -> tuple[str, str, Dict[str, Any]]:
    tool_dict = normalize_external_tool(tool)
    function_obj = tool_dict.get("function") if isinstance(tool_dict.get("function"), dict) else {}
    name = str(function_obj.get("name") or "").strip()
    description = str(function_obj.get("description") or "").strip()
    parameters = function_obj.get("parameters") if isinstance(function_obj.get("parameters"), dict) else {}
    return name, description, parameters


def extract_path_candidates(query: str) -> List[str]:
    candidates: List[str] = []
    for quoted in re.findall(r"['\"]([^'\"]{2,400})['\"]", query):
        candidates.append(quoted)
    for match in re.findall(
        r"(?<![A-Za-z0-9_])((?:[A-Za-z]:)?[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]{1,16})(?![A-Za-z0-9_])",
        query,
    ):
        candidates.append(match)

    ordered: List[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def extract_path_candidate(query: str) -> Optional[str]:
    candidates = extract_path_candidates(query)
    return candidates[0] if candidates else None


def extract_requested_end_line(query: str) -> Optional[int]:
    patterns = [
        r"(\d{1,5})\s+zeilen",
        r"first\s+(\d{1,5})\s+lines",
        r"erste[nr]?\s+(\d{1,5})",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            try:
                return max(1, int(match.group(1)))
            except ValueError:
                return None
    return None


def extract_explicit_content(query: str) -> Optional[str]:
    patterns = [
        r"(?:schreibe\s+exakt\s+diesen\s+inhalt[^:]*:)\s*(.+?)(?:\s+antworte\s+danach|$)",
        r"(?:write\s+exactly\s+this\s+content[^:]*:)\s*(.+?)(?:\s+then\s+reply|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        content = re.sub(r"\s+", " ", match.group(1)).strip()
        if not content:
            continue
        if " RESULT=" in content and "\n" not in content:
            content = content.replace(" RESULT=", "\nRESULT=")
        if " TRACE_ID=" in content and "\n" not in content:
            content = content.replace(" TRACE_ID=", "\nTRACE_ID=")
        return content
    return None


def infer_external_tool_arguments(query: str, parameters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    properties = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
    required = parameters.get("required") if isinstance(parameters.get("required"), list) else []

    if not required:
        # Most tools can still consume a generic query payload.
        return {"query": query}

    args: Dict[str, Any] = {}
    missing_required: List[str] = []

    url_match = re.search(r"https?://\S+", query)
    path_candidate = extract_path_candidate(query)
    requested_end_line = extract_requested_end_line(query)
    explicit_content = extract_explicit_content(query)

    for field in required:
        key = str(field)
        low = key.lower()
        if low in {"query", "prompt", "text", "message", "input", "question"}:
            args[key] = query
        elif low in {"url", "uri"} and url_match:
            args[key] = url_match.group(0)
        elif low in {"path", "filepath", "file_path", "filename"} and path_candidate:
            args[key] = path_candidate
        elif low in {"content", "body", "text"} and explicit_content:
            args[key] = explicit_content
        elif low in {"command", "cmd"}:
            args[key] = query
        elif low in {"tool", "tool_name", "name"}:
            # Do not force-fill with query text; this tends to create invalid calls.
            missing_required.append(key)
        else:
            missing_required.append(key)

    if missing_required:
        return None

    # Add optional generic query when schema declares it.
    if "query" in properties and "query" not in args:
        args["query"] = query
    if "start_line" in properties and "start_line" not in args and requested_end_line:
        args["start_line"] = 1
    if "end_line" in properties and "end_line" not in args and requested_end_line:
        args["end_line"] = requested_end_line
    return args
