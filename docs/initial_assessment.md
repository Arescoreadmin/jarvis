# JARVIS Initial Assessment and Build-Out Roadmap

## Current state

JARVIS already has a strong skeleton for a personal AI operating system:

- **Interaction surfaces:** local voice loop, CLI/stdin fallback, FastAPI chat endpoint, websocket streaming, mode switching, context/memory endpoints, and alert endpoints.
- **Core intelligence loop:** Claude-backed `Brain` with system prompt injection, mode-aware context assembly, streaming responses, tool use, retry handling, and basic commitment extraction.
- **Memory tiers:** SQLite-backed episodic, semantic, procedural, relational/contact, and commitment memory, with optional Chroma vector storage when available.
- **Context and anticipation:** cached context snapshots, proactive alert queue, morning brief, calendar/email/health/finance/home checks, and mode-sensitive interruption rules.
- **Tool layer:** calendar, email, web search, research, tasks, notes, health/Oura, finance/Alpaca, and Home Assistant integrations are registered behind Claude tool schemas.

This is past a prototype prompt: it is an early agent runtime. The next work should focus less on adding more tools and more on reliability, permissions, richer context extraction, and user-visible workflows.

## High-leverage gaps

### 1. Observability and testability are thin

There is no dedicated test suite yet, and several capabilities depend on external services or audio hardware. That makes regressions hard to catch before runtime.

**Impact:** future changes to tool calling, memory writes, or API behavior can silently break core flows.

**Start here:** add unit tests with mocked tools/LLM clients for memory, modes, tool registry, context aggregation, API auth, and commitment extraction.

### 2. Configuration exists, but runtime settings are not fully enforced

`config.yaml` contains values for anticipator cadence, memory, voice, and integrations, but some components still use hard-coded class constants or environment defaults.

**Impact:** JARVIS will feel less controllable as it moves from developer machine to always-on service.

**Start here:** introduce a typed settings object that merges defaults, YAML, and environment variables, then pass that object into core components.

### 3. Tool permissions need a real policy layer

The system prompt says irreversible or sensitive actions require confirmation, and `config.yaml` includes destructive-action confirmation intent, but tools can still expose direct actions like email send, calendar delete, task delete, and home control.

**Impact:** the agent cannot safely become more autonomous until actions are classified and gated consistently.

**Start here:** add an action policy middleware around tool execution with categories such as read-only, reversible write, irreversible write, external communication, money/market action, and physical-world action.

### 4. Commitment extraction is too brittle for a chief-of-staff product

Commitments are currently detected with a regex pattern in `Brain`, which will miss many natural commitments and can misread intent.

**Impact:** one of the highest-value promises in the system — never letting commitments fall through cracks — will be unreliable.

**Start here:** replace regex-only extraction with a structured extractor that can run after each interaction, normalize deadlines, connect commitments to contacts/projects, and store confidence/source text.

### 5. Memory search is underused in response generation

The memory layer stores episodes, semantic facts, contacts, procedures, and commitments, but response-time retrieval appears limited to session history, profile, and procedural shortcuts.

**Impact:** JARVIS may store useful information without actually using it when answering.

**Start here:** add a retrieval step that uses the current user input to pull relevant episodes, contacts, commitments, and semantic facts into the system context with token limits and recency/source metadata.

### 6. The API surface is useful but not yet productized

The FastAPI server exposes health, chat, websocket chat, context, mode, memory, alerts, and token creation. It lacks typed response envelopes, pagination, audit metadata, rate limiting, and a clear frontend/mobile contract.

**Impact:** integrating a dashboard, phone client, or automation layer will become painful as fields change.

**Start here:** define stable API schemas, response envelopes, error conventions, and endpoint-level tests before adding more clients.

### 7. Proactive intelligence needs dedupe, lifecycle, and explainability

The anticipator queues alerts and marks them surfaced, but it does not persist alert state, explain why an alert fired, support snooze/resolve/defer, or dedupe robustly across restarts.

**Impact:** an always-on JARVIS risks becoming noisy or forgetful.

**Start here:** persist alerts in SQLite with status, priority, source object, dedupe key, created/surfaced/expires timestamps, and user action history.

## Recommended build sequence

### Phase 1 — Stabilize the runtime foundation

1. Add a test harness and CI-friendly checks:
   - `pytest` test suite.
   - Mocked Anthropic client tests for tool use and commitment extraction.
   - FastAPI tests for auth and core endpoints.
   - SQLite temp-directory fixtures for memory/tasks.
2. Introduce typed settings and remove scattered hard-coded runtime behavior.
3. Add structured logging and audit events for tool calls, memory writes, alerts, and mode changes.
4. Create a `docs/architecture.md` or expand `system.md` with runtime diagrams, data flows, and security boundaries.

### Phase 2 — Make autonomy safe

1. Add a tool policy layer with confirmation requirements before sensitive actions.
2. Add per-tool capability metadata: read/write/destructive/external/physical/money.
3. Add pending-action objects so JARVIS can ask for confirmation, resume execution, and log the outcome.
4. Add API endpoints to review, approve, reject, snooze, and audit pending actions.

### Phase 3 — Make memory useful, not just persistent

1. Add retrieval orchestration before response generation.
2. Improve commitment extraction with structured fields:
   - `who`, `what`, `deadline`, `project`, `priority`, `source_episode_id`, `confidence`.
3. Add contact/project linking and lightweight entity extraction.
4. Add memory maintenance jobs: summarization, retention, deduplication, and stale fact detection.

### Phase 4 — Upgrade proactive workflows

1. Persist alert state and add snooze/resolve/defer workflows.
2. Build daily briefing generation from calendar, tasks, commitments, health, finance, and active projects.
3. Add meeting-prep workflow:
   - upcoming event detection;
   - attendees/contact lookup;
   - related notes/email/tasks;
   - generated prep brief;
   - follow-up capture after the meeting.
4. Add end-of-day review:
   - completed work;
   - missed commitments;
   - tomorrow's risks;
   - suggested schedule/task adjustments.

### Phase 5 — Build the user-facing command center

1. Add a small web dashboard or TUI showing:
   - current mode;
   - context snapshot;
   - alerts;
   - pending approvals;
   - commitments;
   - recent memory.
2. Add a push channel for proactive alerts.
3. Add onboarding flows for profile, timezone, integrations, preferences, and risk tolerance.

## First concrete implementation tickets

These are the best places to start because they de-risk everything else:

1. **Testing baseline:** add `pytest`, isolate data paths, and cover `Memory`, `ModeManager`, `ToolRegistry`, and `/health` + token-protected API paths.
2. **Settings object:** create `core/settings.py`, load YAML + environment, and pass settings into `Anticipator`, `Voice`, `Brain`, and tool registration.
3. **Tool safety metadata:** extend `ToolBase` with `risk_level`, `requires_confirmation`, and `action_type`; enforce it in `Brain._execute_tool_and_continue`.
4. **Persistent alerts:** add an alerts table/repository and wire `Anticipator` to dedupe by stable keys across restarts.
5. **Memory retrieval:** before every LLM call, retrieve relevant episodes/contacts/commitments and inject a bounded `RELEVANT MEMORY` block.

## Suggested definition of done for the next milestone

A solid next milestone would be: **"JARVIS can safely remember, retrieve, and act with auditable tool usage."**

Acceptance criteria:

- Tests run locally without real API keys or hardware.
- Every tool call is logged with input summary, risk category, result status, and timestamp.
- Sensitive tool calls create pending approvals instead of executing immediately.
- Commitments are extracted into structured records and can be listed, completed, or deferred.
- Responses include relevant stored memory when applicable.
- Alerts survive restarts and can be snoozed or resolved.
