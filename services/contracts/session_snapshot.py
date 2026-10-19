"""Typed transport for Redis session snapshots, separate from chat history."""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class SessionSnapshotReadRequest(BaseModel):
    session_id: str = Field(min_length=1)


class SessionSnapshotWriteRequest(SessionSnapshotReadRequest):
    snapshot: dict[str, Any]
    ttl_seconds: int = Field(default=900, ge=1)

    @model_validator(mode="after")
    def matching_session(self):
        if self.snapshot.get("session_id") != self.session_id:
            raise ValueError("Snapshot session_id must match the requested session")
        return self


class SessionSnapshotResponse(BaseModel):
    snapshot: dict[str, Any] | None
