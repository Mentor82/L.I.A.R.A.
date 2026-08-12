"""FastAPI router for file uploads and artifact serving endpoints."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from services.api.models import FileUploadResponse
from services.shared.attachment_security import extract_text_preview, scan_attachment_bytes
from services.shared.sandboxing import (
    canonicalize_sandbox_root,
    ensure_within_boundary,
    get_global_sandbox_root,
    resolve_sandbox_root,
)


router = APIRouter(prefix="/files", tags=["artifacts"])


def _sanitize_upload_name(filename: str) -> str:
    name = Path(filename).name
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "_", name).strip("._")
    return cleaned or "upload.bin"


def _sanitize_session_segment(session_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id.strip()).strip("_")
    return cleaned or "default"


def _attachment_storage_root(sandbox_root_str: str, session_id: str) -> Path:
    session_segment = _sanitize_session_segment(session_id)
    return Path(sandbox_root_str) / ".liara_uploads" / session_segment


async def _get_session_snapshot_best_effort(adapter: Any, session_id: str) -> dict[str, Any]:
    if not hasattr(adapter, "get_session_snapshot"):
        return {}
    try:
        snapshot = await adapter.get_session_snapshot(session_id)
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


def _resolve_effective_sandbox_root(explicit: str | None, snapshot: dict[str, Any]) -> str:
    candidate = explicit
    if not candidate:
        metadata = snapshot.get("metadata") or {}
        candidate = metadata.get("sandbox_root") if isinstance(metadata, dict) else None
    candidate = candidate or get_global_sandbox_root()
    return canonicalize_sandbox_root(candidate)


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    request: Request,
    response: Response,
    session_id: str = Form(...),
    user_id: str = Form(...),
    file: UploadFile = File(...),
    sandbox_root: str | None = Form(default=None),
) -> FileUploadResponse:
    response.headers["Cache-Control"] = "no-store"
    adapter = request.app.state.memory_adapter
    snapshot = await _get_session_snapshot_best_effort(adapter, session_id)

    try:
        effective_sandbox_root = _resolve_effective_sandbox_root(sandbox_root, snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    max_upload_bytes = max(1024, int(os.getenv("LIARA_UPLOAD_MAX_BYTES", str(5 * 1024 * 1024))))
    char_limit = max(200, int(os.getenv("LIARA_ATTACHMENT_TEXT_CHAR_LIMIT", "12000")))
    raw = await file.read()

    if len(raw) > max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "message": "Uploaded file exceeds configured size limit.",
                "size_bytes": len(raw),
                "max_upload_bytes": max_upload_bytes,
            },
        )

    scan_result = scan_attachment_bytes(raw)
    if scan_result.status == "blocked":
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Uploaded file blocked by malware scanner.",
                "filename": file.filename,
                "scan": scan_result.to_metadata(),
            },
        )

    local_sandbox_root = resolve_sandbox_root(effective_sandbox_root, get_global_sandbox_root())
    storage_root = _attachment_storage_root(str(local_sandbox_root), session_id)
    storage_root.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_upload_name(file.filename or "upload.bin")
    stored_path = storage_root / f"{uuid4().hex}_{safe_name}"
    stored_path.write_bytes(raw)
    stored_path_relative = stored_path.relative_to(local_sandbox_root)
    canonical_stored_path = f"{effective_sandbox_root.rstrip('/')}/{stored_path_relative.as_posix()}"

    text_preview = extract_text_preview(raw, file.content_type, char_limit)
    attachment_payload = {
        "name": safe_name,
        "media_type": file.content_type,
        "text_content": text_preview,
        "size_bytes": len(raw),
        "source": "liara-upload",
        "metadata": {
            "stored_path": canonical_stored_path,
            "stored_path_local": str(stored_path),
            "session_id": session_id,
            "user_id": user_id,
            "sandbox_root": effective_sandbox_root,
            "sandbox_root_local": str(local_sandbox_root),
            "scan": scan_result.to_metadata(),
            "has_text_preview": bool(text_preview),
        },
    }

    return FileUploadResponse(
        attachment=attachment_payload,
        scan=scan_result.to_metadata(),
    )


@router.get("/artifact")
async def read_artifact(
    request: Request,
    response: Response,
    session_id: str = Query(...),
    path: str = Query(...),
    sandbox_root: str | None = Query(default=None),
) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    adapter = request.app.state.memory_adapter
    snapshot = await _get_session_snapshot_best_effort(adapter, session_id)

    try:
        effective_sandbox_root = _resolve_effective_sandbox_root(sandbox_root, snapshot)
        local_sandbox_root = resolve_sandbox_root(effective_sandbox_root, get_global_sandbox_root())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_segment = _sanitize_session_segment(session_id)
    artifact_scope_root = (local_sandbox_root / ".liara_artifacts" / session_segment).resolve()

    try:
        target = (local_sandbox_root / path).resolve()
        ensure_within_boundary(target, local_sandbox_root, "Artifact path escapes sandbox boundary.")
        ensure_within_boundary(target, artifact_scope_root, "Artifact access denied for this session.")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        path=target,
        media_type=media_type,
        filename=target.name,
        headers={"Cache-Control": "private, no-store"},
    )
