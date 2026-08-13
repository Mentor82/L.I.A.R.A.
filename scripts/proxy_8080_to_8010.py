"""Reverse proxy forwarding requests from port 8080 to LIARA API on port 8010."""

import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI(title="LIARA Proxy 8080 -> 8010")

TARGET_BASE_URL = os.getenv("TARGET_BASE_URL", "http://127.0.0.1:8010")


async def _proxy_request(request: Request, path: str):
    client = httpx.AsyncClient(timeout=None)
    url = f"{TARGET_BASE_URL}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)

    body = await request.body()

    req = client.build_request(
        method=request.method,
        url=url,
        headers=headers,
        content=body,
    )

    response = await client.send(req, stream=True)

    resp_headers = dict(response.headers)
    resp_headers.pop("content-length", None)
    resp_headers.pop("content-encoding", None)

    async def body_stream():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_stream(),
        status_code=response.status_code,
        headers=resp_headers,
        media_type=response.headers.get("content-type"),
    )


@app.get("/")
async def root_description(request: Request, proxy: bool = False):
    """Return JSON system description for root '/', or pass-through if proxy=true query is provided."""
    if proxy:
        return await _proxy_request(request, "")

    return JSONResponse(
        status_code=200,
        content={
            "service": "LIARA API Proxy & Gateway",
            "version": "2026-08-13",
            "status": "online",
            "description": "LIARA Modular AI Orchestration Platform Gateway",
            "available_endpoints": {
                "health": "/health",
                "backends_health": "/health/backends",
                "chat": "/chat",
                "chat_stream": "/chat/stream",
                "history": "/history",
                "session": "/session",
                "tools": "/tools",
                "governance_proposals": "/tools/sys/governance/proposals",
                "speech_generate": "/speech/generate",
                "speech_stream": "/speech/stream",
                "compute_models": "/compute/models",
                "operations_heartbeat": "/operations/heartbeat",
                "architecture_subgraph": "/operations/graph/subgraph",
            },
        },
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_catchall(request: Request, path: str):
    return await _proxy_request(request, path)


if __name__ == "__main__":
    port = int(os.getenv("PROXY_PORT", "8080"))
    host = os.getenv("PROXY_HOST", "0.0.0.0")
    print(f"Starting LIARA Proxy on http://{host}:{port} -> {TARGET_BASE_URL}")
    uvicorn.run(app, host=host, port=port)
