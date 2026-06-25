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
from pathlib import Path
from typing import AsyncIterator

import jwt
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

UI_PATH = Path(__file__).parent / "ui" / "index.html"

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


def register_components(
    brain, memory, context, modes, anticipator, tool_registry=None,
    relationship_engine=None, push_notifier=None,
) -> None:
    _jarvis_components.update({
        "brain": brain,
        "memory": memory,
        "context": context,
        "modes": modes,
        "anticipator": anticipator,
        "tools": tool_registry or getattr(brain, "_tools", None),
        "relationships": relationship_engine,
        "push": push_notifier,
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

class ActionDecisionRequest(BaseModel):
    action_id: str

class WatchRequest(BaseModel):
    description: str
    tool_name: str
    tool_args: dict = {}
    condition: str
    priority: str = "medium"
    recur: bool = True

class WatchRemoveRequest(BaseModel):
    watch_id: str

class VisionRequest(BaseModel):
    message: str
    images: list[dict]  # [{"base64": "...", "media_type": "image/jpeg"}]

class ContactUpsertRequest(BaseModel):
    name: str
    relationship: str = ""
    email: str = ""
    phone: str = ""
    communication_style: str = ""
    notes: str = ""

class InteractionRequest(BaseModel):
    person_name: str
    interaction_type: str = "note"
    summary: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def ui():
    if UI_PATH.exists():
        return HTMLResponse(content=UI_PATH.read_text())
    return HTMLResponse(content="<h1>UI not found</h1>", status_code=404)


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


@app.get("/actions/pending")
async def get_pending_actions(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503, detail="Memory not available")
    return {"actions": memory.get_pending_actions()}


@app.post("/actions/approve")
async def approve_action(req: ActionDecisionRequest, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    tool_registry = _jarvis_components.get("tools")
    if not memory or not tool_registry:
        raise HTTPException(status_code=503, detail="JARVIS not initialized")

    action = memory.get_pending_action(req.action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Action already {action['status']}")

    tool = tool_registry.get(action["tool_name"])
    if not tool:
        memory.resolve_pending_action(req.action_id, "failed", "Tool no longer available")
        raise HTTPException(status_code=404, detail="Tool no longer available")

    try:
        result = await tool.run(**action["args"])
    except Exception as e:
        result = f"Error: {e}"
        memory.resolve_pending_action(req.action_id, "failed", result)
        return {"status": "failed", "result": result}

    memory.resolve_pending_action(req.action_id, "executed", str(result))
    return {"status": "executed", "result": str(result)}


@app.post("/actions/reject")
async def reject_action(req: ActionDecisionRequest, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503, detail="Memory not available")

    action = memory.get_pending_action(req.action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Action already {action['status']}")

    memory.resolve_pending_action(req.action_id, "rejected", "Rejected by user")
    return {"status": "rejected"}


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


# ── Executor ──────────────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    goal: str


@app.post("/execute")
async def execute_goal(req: ExecuteRequest, _: str = Depends(verify_token)):
    """Run a multi-step autonomous goal and return the full execution log."""
    from agents.executor import Executor
    brain = _jarvis_components.get("brain")
    memory = _jarvis_components.get("memory")
    ctx = _jarvis_components.get("context")
    modes = _jarvis_components.get("modes")
    tools = _jarvis_components.get("tools")

    if not all([brain, memory, ctx, modes, tools]):
        raise HTTPException(status_code=503, detail="JARVIS not fully initialized")

    executor = Executor(memory, modes, ctx, tools)
    chunks = []
    async for chunk in executor.run(req.goal):
        chunks.append(chunk)
    return {"log": "".join(chunks)}


@app.websocket("/ws/execute")
async def ws_execute(websocket: WebSocket):
    """Stream execution progress over WebSocket."""
    token = websocket.query_params.get("token", "")
    try:
        jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    from agents.executor import Executor
    memory = _jarvis_components.get("memory")
    ctx = _jarvis_components.get("context")
    modes = _jarvis_components.get("modes")
    tools = _jarvis_components.get("tools")

    if not all([memory, ctx, modes, tools]):
        await websocket.send_text("[JARVIS not initialized]")
        await websocket.close()
        return

    try:
        goal = await websocket.receive_text()
        executor = Executor(memory, modes, ctx, tools)
        async for chunk in executor.run(goal):
            await websocket.send_text(chunk)
        await websocket.send_text("\x00")
    except WebSocketDisconnect:
        pass


# ── Vision ────────────────────────────────────────────────────────────────────

@app.post("/chat/vision")
async def chat_vision(req: VisionRequest, _: str = Depends(verify_token)):
    brain = _jarvis_components.get("brain")
    if not brain:
        raise HTTPException(status_code=503, detail="JARVIS not initialized")
    response_parts = []
    async for chunk in await brain.respond(req.message, images=req.images):
        response_parts.append(chunk)
    return {"response": "".join(response_parts)}


# ── Relationships ─────────────────────────────────────────────────────────────

@app.get("/relationships")
async def get_relationships(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    return {"contacts": memory.get_all_contacts()}


@app.post("/relationships/contact")
async def upsert_contact(req: ContactUpsertRequest, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    fields = {k: v for k, v in req.model_dump().items() if v and k != "name"}
    cid = memory.upsert_contact(req.name, **fields)
    return {"id": cid, "result": f"Contact saved: {req.name}"}


@app.post("/relationships/interaction")
async def record_interaction(req: InteractionRequest, _: str = Depends(verify_token)):
    rel = _jarvis_components.get("relationships")
    if not rel:
        raise HTTPException(status_code=503, detail="Relationship engine not initialized")
    rel.record_interaction(req.person_name, req.interaction_type, req.summary)
    return {"result": f"Interaction recorded with {req.person_name}"}


@app.get("/relationships/drift")
async def get_drift_alerts(_: str = Depends(verify_token)):
    rel = _jarvis_components.get("relationships")
    if not rel:
        raise HTTPException(status_code=503, detail="Relationship engine not initialized")
    return {"drifted": rel.get_drift_alerts()}


@app.get("/relationships/brief/{event_title}")
async def get_meeting_brief(event_title: str, attendees: str = "", _: str = Depends(verify_token)):
    rel = _jarvis_components.get("relationships")
    if not rel:
        raise HTTPException(status_code=503)
    names = [n.strip() for n in attendees.split(",") if n.strip()]
    brief = rel.get_pre_meeting_brief(names, event_title)
    return {"brief": brief}


# ── Watchlist ─────────────────────────────────────────────────────────────────

@app.get("/watchlist")
async def get_watchlist(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    return {"watches": memory.watchlist.get_all()}


@app.post("/watchlist")
async def add_watch(req: WatchRequest, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    watch_id = memory.watchlist.add(
        description=req.description,
        tool_name=req.tool_name,
        tool_args=req.tool_args,
        condition=req.condition,
        priority=req.priority,
        recur=req.recur,
    )
    return {"watch_id": watch_id, "result": f"Now watching: {req.description}"}


@app.delete("/watchlist/{watch_id}")
async def remove_watch(watch_id: str, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    removed = memory.watchlist.remove(watch_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Watch not found")
    return {"result": "Watch removed"}


# ── Distiller ─────────────────────────────────────────────────────────────────

@app.post("/distill")
async def run_distiller(force: bool = False, _: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    from core.distiller import Distiller
    distiller = Distiller(memory)
    result = await distiller.run(force=force)
    return {"result": result}


# ── Strategic Planning ────────────────────────────────────────────────────────

class ObjectiveRequest(BaseModel):
    title: str
    description: str = ""
    horizon: str = "quarterly"
    owner: str = ""
    start_date: str = ""
    end_date: str = ""

class KeyResultRequest(BaseModel):
    objective_id: str
    title: str
    target_value: float
    baseline_value: float = 0.0
    unit: str = ""

class ProgressRequest(BaseModel):
    kr_id: str
    current_value: float
    note: str = ""

class StatusRequest(BaseModel):
    objective_id: str
    status: str

class DraftOKRRequest(BaseModel):
    description: str


@app.get("/strategy/objectives")
async def get_objectives(horizon: str = "", status: str = "", _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    return {"objectives": engine.list_objectives(
        horizon=horizon or None,
        status=status or None,
    )}


@app.get("/strategy/objectives/{objective_id}")
async def get_objective(objective_id: str, _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    obj = engine.get_objective(objective_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Objective not found")
    return obj


@app.post("/strategy/objective")
async def create_objective(req: ObjectiveRequest, _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    oid = engine.add_objective(
        title=req.title,
        description=req.description,
        horizon=req.horizon,
        owner=req.owner,
        start_date=req.start_date,
        end_date=req.end_date,
    )
    return {"id": oid, "result": f"Objective created: {req.title}"}


@app.post("/strategy/key-result")
async def add_key_result(req: KeyResultRequest, _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    krid = engine.add_key_result(
        objective_id=req.objective_id,
        title=req.title,
        target_value=req.target_value,
        baseline_value=req.baseline_value,
        unit=req.unit,
    )
    return {"id": krid, "result": f"Key result added: {req.title}"}


@app.put("/strategy/kr/progress")
async def update_kr_progress(req: ProgressRequest, _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    try:
        result = engine.update_progress(req.kr_id, req.current_value, req.note)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/strategy/objective/status")
async def set_objective_status(req: StatusRequest, _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    engine.set_objective_status(req.objective_id, req.status)
    return {"result": f"Status updated to: {req.status}"}


@app.get("/strategy/at-risk")
async def get_at_risk(_: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    return {"at_risk": engine.get_at_risk()}


@app.get("/strategy/weekly-review")
async def get_weekly_review(_: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    review = await engine.generate_weekly_review()
    return {"review": review}


@app.post("/strategy/draft-okrs")
async def draft_okrs(req: DraftOKRRequest, _: str = Depends(verify_token)):
    from core.strategy import StrategyEngine
    engine = StrategyEngine()
    draft = await engine.generate_okrs_from_description(req.description)
    return {"draft": draft}


# ── Goals ─────────────────────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    title: str
    description: str = ""
    deadline: str = ""
    priority: str = "medium"
    linked_objective_id: str = ""


class MilestoneRequest(BaseModel):
    goal_id: str
    title: str
    due_date: str = ""


class GoalStatusRequest(BaseModel):
    status: str


@app.get("/goals")
async def list_goals(_: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    return {"goals": engine.get_active_goals()}


@app.post("/goals")
async def create_goal(req: GoalRequest, _: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    gid = engine.add_goal(
        title=req.title,
        description=req.description,
        deadline=req.deadline,
        priority=req.priority,
        linked_objective_id=req.linked_objective_id,
    )
    return {"id": gid, "title": req.title}


@app.get("/goals/blocked")
async def get_blocked_goals(_: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    return {"blocked": engine.get_blocked()}


@app.get("/goals/{goal_id}")
async def get_goal(goal_id: str, _: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    g = engine.get_goal(goal_id)
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found")
    return g


@app.put("/goals/{goal_id}/status")
async def set_goal_status(goal_id: str, req: GoalStatusRequest, _: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    engine.set_goal_status(goal_id, req.status)
    return {"id": goal_id, "status": req.status}


@app.post("/goals/{goal_id}/milestones")
async def add_milestone(goal_id: str, req: MilestoneRequest, _: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    mid = engine.add_milestone(goal_id=goal_id, title=req.title, due_date=req.due_date)
    return {"id": mid, "title": req.title}


@app.put("/goals/milestones/{milestone_id}/complete")
async def complete_milestone(milestone_id: str, _: str = Depends(verify_token)):
    from core.goals import GoalEngine
    engine = GoalEngine()
    result = engine.complete_milestone(milestone_id)
    return result


# ── Developer CLI hooks ────────────────────────────────────────────────────────

class DevEventRequest(BaseModel):
    source: str                  # claude-code | codex
    event_type: str              # PostToolUse | Stop | session_start | session_end
    session_id: str = ""
    cwd: str = ""
    payload: dict = {}


@app.post("/events/dev")
async def ingest_dev_event(req: DevEventRequest, _: str = Depends(verify_token)):
    """
    Receives hook events from Claude Code CLI and OpenAI Codex CLI.
    Fire-and-forget from the CLI side — always returns 200 immediately.
    """
    memory = _jarvis_components.get("memory")
    if memory:
        try:
            memory.dev.ingest(req.model_dump())
            ctx = _jarvis_components.get("context")
            if ctx:
                ctx.invalidate()
        except Exception:
            pass
    return {"ok": True}


@app.get("/dev/context")
async def get_dev_context(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    return {
        "active_session": memory.dev.get_active_session(),
        "recent_sessions": memory.dev.get_recent_sessions(hours=24),
        "context_block": memory.dev.build_context_block(),
    }


@app.get("/dev/stats")
async def get_dev_stats(_: str = Depends(verify_token)):
    memory = _jarvis_components.get("memory")
    if not memory:
        raise HTTPException(status_code=503)
    return {"project_stats": memory.dev.get_project_stats(days=7)}
