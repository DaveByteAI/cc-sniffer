import os
import json
import time
import uuid
import asyncio
from pathlib import Path
from typing import Dict
import hashlib
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
from starlette.background import BackgroundTask

app = FastAPI()

LOG_DIR = Path("llm-sniffer-logs")
LOG_DIR.mkdir(exist_ok=True)

OPENAI_TARGET = os.getenv("OPENAI_TARGET", "https://api.openai.com")
ANTHROPIC_TARGET = os.getenv("ANTHROPIC_TARGET", "https://api.deepseek.com/anthropic")

# SSE event bus — pushes lightweight notifications for new intercepted requests
event_queue: asyncio.Queue = asyncio.Queue()


def clean_headers(headers: Dict[str, str]) -> Dict[str, str]:
    blocked = {"host", "content-length", "connection", "accept-encoding"}
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = dict(headers)
    for k in list(out.keys()):
        if k.lower() in {"authorization", "x-api-key", "api-key"}:
            out[k] = "***REDACTED***"
    return out


def choose_target(path: str) -> str:
    if path.startswith("/v1/messages") or path.startswith("/v1/models"):
        return ANTHROPIC_TARGET
    if path.startswith("/v1/responses") or path.startswith("/v1/chat/completions"):
        return OPENAI_TARGET
    return OPENAI_TARGET


# ── UI & API routes (MUST be registered before the catch-all proxy) ──────────

@app.get("/")
async def serve_ui():
    return FileResponse("sniffer_ui.html")


@app.get("/api/logs")
async def list_logs():
    """Return metadata for the 500 most recent log files."""
    files = sorted(LOG_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:500]
    items = []
    for f in files:
        try:
            head = json.loads(f.read_text("utf-8"))
            if isinstance(head, dict):
                items.append({
                    "filename": f.name,
                    "time": head.get("time", ""),
                    "method": head.get("method", ""),
                    "path": head.get("path", ""),
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
        except Exception:
            pass
    return items


@app.get("/api/logs/{filename}")
async def get_log(filename: str):
    """Return the full JSON content of a single log file."""
    if not filename.endswith(".json"):
        return {"error": "invalid filename"}
    path = LOG_DIR / filename
    if not path.exists():
        return {"error": "not found"}
    return json.loads(path.read_text("utf-8", errors="replace"))


@app.get("/api/events")
async def event_stream(request: Request):
    """SSE endpoint: pushes a message to the browser each time a request is sniffed."""
    async def generate():
        while True:
            try:
                data = await asyncio.wait_for(event_queue.get(), timeout=30)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Proxy ────────────────────────────────────────────────────────────────────

async def dump_request(method: str, path: str, headers: Dict[str, str], body: bytes):
    ts = time.strftime("%Y%m%d-%H%M%S")
    rid = str(uuid.uuid4())[:8]
    filename = LOG_DIR / f"{ts}-{rid}.json"

    body_text = body.decode("utf-8", errors="replace")
    body_sha256 = hashlib.sha256(body).hexdigest()

    try:
        parsed_body = json.loads(body_text) if body else None
    except Exception:
        parsed_body = None

    record = {
        "time": ts,
        "method": method,
        "path": path,
        "headers": redact_headers(headers),
        "body_sha256": body_sha256,
        "body": parsed_body,
    }

    filename.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[sniff] saved {filename}")

    # Push lightweight notification to SSE clients
    await event_queue.put({
        "type": "new_log",
        "filename": filename.name,
        "time": ts,
        "method": method,
        "path": path,
        "has_body": bool(parsed_body),
    })


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    raw_path = "/" + path
    query = request.url.query
    if query:
        raw_path += "?" + query

    body = await request.body()
    incoming_headers = dict(request.headers)

    await dump_request(
        method=request.method,
        path=raw_path,
        headers=incoming_headers,
        body=body,
    )

    target_base = choose_target("/" + path)
    upstream_url = target_base.rstrip("/") + "/" + path
    if query:
        upstream_url += "?" + query

    headers = clean_headers(incoming_headers)

    async_client = httpx.AsyncClient(timeout=None)
    upstream_req = async_client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=body,
    )
    upstream_resp = await async_client.send(upstream_req, stream=True)
    response_headers = clean_headers(dict(upstream_resp.headers))

    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=response_headers,
        background=BackgroundTask(async_client.aclose),
    )
