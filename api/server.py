"""
FastAPI remote access server.

Exposes JARVIS over HTTP/WebSocket for phone, browser, or travel access.
JWT-authenticated. Streaming responses via WebSocket.

Endpoints:
  POST /chat          — single-turn text exchange
  WS   /ws/chat       — streaming text session
  GET  /context       — current context snapshot
  GET  /mode          — current mode
  PUT  /mode          — set mode
  GET  /memory/commitments — pending commitments
  GET  /memory/contacts    — contact list
  POST /memory/note        — add procedural note / shortcut
  GET  /health             — server health check
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import jwt
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

SECRET = os.environ.get("JARVIS_API_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 168  # 1 week

app = FastAPI(title="JARVIS Remote API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

_jarvis_components: dict = {}  # populated by main.py on startup


def register_components(brain, memory, context, modes, anticipator) -> None:
    _jarvis_components.update({
        "brain": brain,
        "memory": memory,
        "context": context,
        "modes": modes,
        "anticipator": anticipator,
    })


# ── Auth ──────────────────────────────────────────────────────────────────────

def create_token(sub: str = "jarvis") -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": sub, "exp": expire}, SECRET, algorithm=ALGORITHM)


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=[ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str

class ModeRequest(BaseModel):
    mode: str

class NoteRequest(BaseModel):
    trigger: str
    expansion: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "online", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/chat")
async def chat(req: ChatRequest, _: str = Depends(verify_token)):
    brain = _jarvis_components.get("brain")
    if not brain:
        raise HTTPException(status_code=503, detail="JARVIS not initialized")
    response_parts = []
    async for chunk in await brain.respond(req.message):
        response_parts.append(chunk)
    return {"response": "".join(response_parts)}


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    try:
        jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    brain = _jarvis_components.get("brain")
    if not brain:
        await websocket.send_text("[JARVIS not initialized]")
        await websocket.close()
        return

    try:
        while True:
            message = await websocket.receive_text()
            async for chunk in await brain.respond(message):
                await websocket.send_text(chunk)
            await websocket.send_text("\x00")  # end-of-response marker
    except WebSocketDisconnect:
        pass


@app.get("/context")
async def get_context(_: str = Depends(verify_token)):
    ctx = _jarvis_components.get("context")
    if not ctx:
        raise HTTPException(status_code=503, detail="Context aggregator not available")
    snap = await ctx.get()
    return {
        "timestamp": snap.timestamp,
        "mode": snap.active_mode,
        "upcoming_events": snap.upcoming_events,
        "unread_email": snap.unread_email_count,
        "pending_tasks": snap.pending_task_count,
        "commitments_due_soon": snap.commitments_due_soon,
        "overdue_commitments": snap.overdue_commitments,
        "health_note": snap.health_note,
        "financial_alert": snap.financial_alert,
        "alerts": [
            {"priority": a.priority, "message": a.message}
            for a in snap.proactive_alerts
        ],
    }


@app.get("/mode")
async def get_mode(_: str = Depends(verify_token)):
    modes = _jarvis_components.get("modes")
    if not modes:
        raise HTTPException(status_code=503)
    return {"mode": modes.current.value}


@app.put("/mode")
async def set_mode(req: ModeRequest, _: str = Depends(verify_token)):
    modes = _jarvis_components.get("modes")
    if not modes:
        raise HTTPException(status_code=503)
    result = modes.set(modes.from_string(req.mode))
    return {"result": result}


@app.get("/memory/commitments")
async def get_commitments(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    return {
        "pending": memory.get_pending_commitments(),
        "overdue": memory.get_overdue_commitments(),
    }


@app.get("/memory/contacts")
async def get_contacts(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    return {"contacts": memory.get_all_contacts()}


@app.post("/memory/note")
async def add_procedure(req: NoteRequest, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    memory.add_procedure(req.trigger, req.expansion)
    return {"result": f"Shortcut saved: '{req.trigger}' → {req.expansion}"}


@app.get("/alerts")
async def get_alerts(_: str = Depends(verify_token)):
    anticipator = _jarvis_components.get("anticipator")
    if not anticipator:
        raise HTTPException(status_code=503)
    alerts = anticipator.get_pending_alerts(min_priority="medium")
    return {
        "alerts": [
            {"priority": a.priority, "category": a.category, "message": a.message}
            for a in alerts
        ]
    }


@app.post("/token")
async def get_token(api_key: str):
    if api_key != os.environ.get("JARVIS_API_SECRET", ""):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"token": create_token()}
