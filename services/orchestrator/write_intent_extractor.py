"""LLM-based extraction for Write-Intent parameters when explicit patterns don't match.

When Stage 2 (select_sys_command) detects Write-Intent (_WRITE_KW) but none of the
explicit patterns (_WRITE_QUOTED_RE, _EMPTY_FILE_RE, _DIR_RE) match, this module
uses a lightweight LLM call to extract:
  - target_path (filename or directory)
  - content (if applicable; for file writes)
  - write_mode (overwrite/append/mkdir/touch)

This enables flexible natural-language write requests without hardcoding all variations.
"""

import re
import logging
from typing import Optional

_LOGGER = logging.getLogger("liara.orchestrator.write_intent_extractor")


def extract_write_intent_parameters(query: str, inference_invoker=None) -> Optional[dict]:
    """Try to extract write-intent parameters via lightweight LLM call.

    Args:
        query: The user's chat query.
        inference_invoker: Inference service to use for LLM extraction.

    Returns:
        Dict with keys: target_path, content (optional), write_mode, storage_scope
        Or None if extraction fails / no write intent detected.
    """
    if not inference_invoker:
        return None

    q_lower = query.lower()

    # Quick heuristic: does this look like a write request?
    write_kw = {"write", "save", "schreibe", "speichere", "erstelle", "create", "anlegen"}
    if not any(kw in q_lower for kw in write_kw):
        return None

    # Build extraction prompt
    extraction_prompt = f"""Extract file write parameters from this user request. Respond ONLY with JSON.

User request: {query}

Extract and return a JSON object with exactly these keys:
- "target_path": filename or directory path (e.g., "test.py", "config/app.json", "my_folder")
- "content": file content if provided, otherwise null
- "write_mode": one of "overwrite", "append", "mkdir", "touch", "unknown"
- "storage_scope": "workspace" or "temp" based on path hints

Example responses:
{{"target_path": "test.py", "content": "print('hello')", "write_mode": "overwrite", "storage_scope": "workspace"}}
{{"target_path": "my_folder", "content": null, "write_mode": "mkdir", "storage_scope": "workspace"}}
{{"target_path": "log.txt", "content": null, "write_mode": "touch", "storage_scope": "temp"}}

If you cannot extract clear parameters, return {{"target_path": null}}.

Now extract from: {query}
"""

    try:
        # Call inference service for extraction
        result = inference_invoker.invoke(
            model="fallback",  # or detect from config
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=200,
            temperature=0.3,  # Low temp for deterministic extraction
        )

        if not result or not result.get("choices"):
            return None

        response_text = result["choices"][0].get("message", {}).get("content", "").strip()
        if not response_text:
            return None

        # Parse JSON from response
        import json
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if not json_match:
            return None

        parsed = json.loads(json_match.group())

        # Validate extracted fields
        target_path = parsed.get("target_path")
        if not target_path:
            return None

        # Sanitize path
        target_path = str(target_path).strip().strip('"').strip("'")

        content = parsed.get("content")
        write_mode = parsed.get("write_mode", "unknown")
        storage_scope = parsed.get("storage_scope", "workspace")

        # Validate write_mode
        if write_mode not in {"overwrite", "append", "mkdir", "touch", "unknown"}:
            write_mode = "unknown"

        # Validate storage_scope
        if storage_scope not in {"workspace", "temp"}:
            storage_scope = "workspace"

        _LOGGER.debug(
            f"Extracted write intent: path={target_path}, mode={write_mode}, scope={storage_scope}"
        )

        return {
            "target_path": target_path,
            "content": content,
            "write_mode": write_mode,
            "storage_scope": storage_scope,
        }

    except Exception as e:
        _LOGGER.warning(f"LLM extraction failed: {e}")
        return None


def resolve_managed_target_from_extracted(
    target_path: str,
    storage_scope: str,
) -> str:
    """Resolve a user-provided path fragment to a managed workspace path.

    Args:
        target_path: User-provided filename or path fragment.
        storage_scope: "workspace" or "temp".

    Returns:
        Full managed path (e.g., /home/liara/workspace/test.py).
    """
    target_path = target_path.strip().strip('"').strip("'")

    # If user already gave full path, use it (if it matches our scopes)
    if target_path.startswith("/home/liara/temp/"):
        return target_path
    if target_path.startswith("/home/liara/workspace/"):
        return target_path

    # Otherwise, build from scope
    base = "/home/liara/temp" if storage_scope == "temp" else "/home/liara/workspace"
    return f"{base}/{target_path.lstrip('/')}"
