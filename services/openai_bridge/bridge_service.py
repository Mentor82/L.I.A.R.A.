import os
import base64
import hashlib
import uuid
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from urllib.parse import unquote_to_bytes

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

LIARA_API_URL = os.getenv("LIARA_API_URL", "http://127.0.0.1:8010/chat")
BRIDGE_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8011"))
BRIDGE_MAX_IMAGE_BYTES = int(os.getenv("BRIDGE_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))

app = FastAPI(title="LIARA OpenAI Bridge", version="1.0.0")


# -------------------------------------------------------------------
# OpenAI-like Schemas (minimal)
# -------------------------------------------------------------------

class OpenAIMessage(BaseModel):
    role: str
    content: Any


class OpenAIChatRequest(BaseModel):
    model: Optional[str] = None
    messages: List[OpenAIMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = False
    user: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    sandbox_root: Optional[str] = None
    session_id: Optional[str] = None


class OpenAIChatChoice(BaseModel):
    index: int
    message: OpenAIMessage
    finish_reason: str = "stop"


class OpenAIChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    model: str
    choices: List[OpenAIChatChoice]


# -------------------------------------------------------------------
# LIARA Schemas (aus API_REFERENCE abgeleitet, minimal)
# -------------------------------------------------------------------

class LiaraChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str
    attachments: Optional[List[Dict[str, Any]]] = None
    tools_override: Optional[List[str]] = None
    available_tools: Optional[List[Dict[str, Any]]] = None
    allow_external_tool_calls: bool = False
    tool_results: Optional[List[Dict[str, Any]]] = None
    max_tokens: Optional[int] = 2048
    preferred_provider: Optional[str] = None
    preferred_model: Optional[str] = None
    request_source: Optional[str] = None
    risk_reassessment: bool = False
    sandbox_root: Optional[str] = None
    user_feedback_score: Optional[float] = None
    user_feedback_stars: Optional[int] = None


class LiaraChatResponse(BaseModel):
    run_id: str
    response: str
    tools_used: Optional[List[str]] = None
    tool_outputs: Optional[Dict[str, Any]] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    ttft_ms: Optional[float] = None
    gen_ms: Optional[float] = None
    validation_passed: Optional[bool] = None
    pending_tool_calls: Optional[List[Dict[str, Any]]] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


# -------------------------------------------------------------------
# Helper: Mapping OpenAI → LIARA
# -------------------------------------------------------------------

def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p).strip()
    return ""


def extract_user_message(req: OpenAIChatRequest) -> str:
    # letzte user-Nachricht
    for m in reversed(req.messages):
        if m.role == "user":
            return _extract_text_content(m.content)
    return ""


def ensure_session_id(req: OpenAIChatRequest) -> str:
    return req.session_id or str(uuid.uuid4())


def ensure_user_id(req: OpenAIChatRequest) -> str:
    return req.user or "anonymous"


def map_tools(req: OpenAIChatRequest) -> List[Dict[str, Any]]:
    return req.tools or []


def map_attachments(req: OpenAIChatRequest) -> List[Dict[str, Any]]:
    attachments = list(req.attachments or [])
    for message in req.messages:
        if not isinstance(message.content, list):
            continue
        for item in message.content:
            if not isinstance(item, dict) or str(item.get("type") or "").lower() not in {"image_url", "input_image"}:
                continue
            image_url = item.get("image_url") or {}
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if not isinstance(url, str) or not url:
                continue
            attachment: Dict[str, Any] = {
                "name": item.get("name") or item.get("filename"),
                "media_type": item.get("media_type") or item.get("mime_type"),
                "source": "openai-bridge-service",
                "metadata": {},
            }
            if url.startswith("data:") and "," in url:
                header, encoded = url[5:].split(",", 1)
                media_type = header.split(";")[0] or attachment["media_type"]
                try:
                    raw = base64.b64decode(encoded, validate=True) if ";base64" in header else unquote_to_bytes(encoded)
                except Exception:
                    continue
                if not str(media_type or "").startswith("image/") or not raw or len(raw) > BRIDGE_MAX_IMAGE_BYTES:
                    continue
                attachment.update({
                    "media_type": media_type,
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "size_bytes": len(raw),
                })
                attachment["metadata"]["sha256"] = hashlib.sha256(raw).hexdigest()
            else:
                attachment["content_url"] = url
            attachments.append(attachment)
    return attachments


def map_model(req: OpenAIChatRequest) -> Optional[str]:
    model = (req.model or "").strip()
    # Keep LIARA backend routing defaults unless the caller provided a concrete model.
    if not model or model.lower() == "liara-agent":
        return None
    return model


def map_provider(req: OpenAIChatRequest) -> Optional[str]:
    provider = os.getenv("BRIDGE_PREFERRED_PROVIDER", "").strip()
    return provider or None


def build_liara_request(req: OpenAIChatRequest) -> LiaraChatRequest:
    # Browser-Tabs / edge_all_open_tabs hart verwerfen
    metadata = req.metadata or {}
    metadata.pop("edge_all_open_tabs", None)

    tools = map_tools(req)
    attachments = map_attachments(req)
    preferred_model = map_model(req)
    preferred_provider = map_provider(req)

    payload: Dict[str, Any] = {
        "session_id": ensure_session_id(req),
        "user_id": ensure_user_id(req),
        "message": extract_user_message(req),
        "attachments": attachments,
        "available_tools": tools,
        "allow_external_tool_calls": bool(tools),
        "max_tokens": req.max_tokens or 2048,
        "request_source": "openai_bridge_service",
        "risk_reassessment": True,
        "sandbox_root": req.sandbox_root,
    }
    if preferred_model:
        payload["preferred_model"] = preferred_model
    if preferred_provider:
        payload["preferred_provider"] = preferred_provider

    return LiaraChatRequest(**payload)


# -------------------------------------------------------------------
# Helper: Mapping LIARA → OpenAI
# -------------------------------------------------------------------

def map_liara_to_openai(liara: LiaraChatResponse) -> OpenAIChatResponse:
    msg = OpenAIMessage(role="assistant", content=liara.response)
    choice = OpenAIChatChoice(index=0, message=msg)
    return OpenAIChatResponse(
        id=liara.run_id,
        model=liara.llm_model or "unknown",
        choices=[choice],
    )


# -------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------

@app.get("/v1/models")
def list_models():
    # Minimaler Dummy – Continue will oft nur wissen "gibt es das Modell?"
    return {
        "object": "list",
        "data": [
            {
                "id": "qwen2.5-3b",
                "object": "model",
                "owned_by": "liara",
            }
        ],
    }


@app.post("/v1/chat/completions", response_model=OpenAIChatResponse)
def chat_completions(openai_req: OpenAIChatRequest):
    liara_req = build_liara_request(openai_req)

    try:
        resp = requests.post(LIARA_API_URL, json=liara_req.dict(), timeout=240)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LIARA unreachable: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    liara_resp = LiaraChatResponse(**resp.json())
    return map_liara_to_openai(liara_resp)


@app.get("/health")
def health():
    return {"status": "ok", "service": "liara-openai-bridge"}


# -------------------------------------------------------------------
# Entrypoint (für uvicorn)
# -------------------------------------------------------------------

# Start:
#   uvicorn services.openai_bridge.bridge_service:app --host 0.0.0.0 --port 8011
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=BRIDGE_HOST, port=BRIDGE_PORT)

