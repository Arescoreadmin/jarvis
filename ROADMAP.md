# Jarvis — Build Roadmap

> **For Claude / Codex:** Before starting any new PR, read this file first.
> Find the next unchecked item in **Upcoming**, implement it, mark it `[x]`, move it to **Completed**, and update the **Last Updated** date.
> Do not skip items or reorder without explicit instruction from the owner.

**Last Updated:** 2026-06-25
**Current Phase:** Foundation → Intelligence Layer

---

## How to Use This File

1. **Read Completed** — understand what is already built before touching anything
2. **Read the next Upcoming item** — that is your task
3. **Check the item off** when the PR merges: move it to Completed, fill in the PR link
4. **Do not start item N+1** until item N is merged

---

## Completed

| # | Feature | PR | Merged |
|---|---------|-------|--------|
| 1 | Core runtime — Brain, Memory (7-tier SQLite), Mode Manager, Context Aggregator | — | 2026-06-25 |
| 2 | Tool Registry + Safety Framework (14 tools: GitHub, Git Ops, Calendar, Gmail, Tasks, Notes, Home, Finance, Health, Web Search, Research, Code Assistant, Architect, Business) | — | 2026-06-25 |
| 3 | Proactive Anticipator (background loop: commitments, calendar, email, health, finance, home, relationships, watchlist) | — | 2026-06-25 |
| 4 | Autonomous Executor (multi-step goal planner + dependency-ordered execution) | — | 2026-06-25 |
| 5 | Remote API (FastAPI + WebSocket: chat, vision, context, mode, memory, approvals, watchlist, relationships, dev hooks) | — | 2026-06-25 |
| 6 | Distiller (nightly episodic→semantic behavioral extraction) | — | 2026-06-25 |
| 7 | Relationship Engine (contact graph, drift detection, pre-meeting briefs) | — | 2026-06-25 |
| 8 | Developer Context Tracker (Claude Code CLI hook integration) | — | 2026-06-25 |
| 9 | **PR Orchestrator** — autonomous PR lifecycle: branch → code gen → push → CI monitor → auto-fix (3x) → merge → local test validate | [#7](https://github.com/Arescoreadmin/jarvis/pull/7) | 2026-06-25 |

---

## Upcoming

Work these in order. Each item below is one PR.

---

### PR-10 — Strategic Planning Layer
**Branch:** `feature/strategic-planning-layer`

Build Jarvis's strategic operating context. He needs to know what the business is trying to achieve at the macro level so every decision, recommendation, and autonomous action is aligned to real objectives.

**Deliverables:**
- `core/strategy.py` — `StrategyEngine` class
  - Load and persist strategic plans (OKRs, quarterly themes, annual goals) to SQLite
  - `get_active_plan()` — returns current strategic context as a prompt block
  - `add_objective(title, key_results, horizon)` — horizon: `weekly | monthly | quarterly | annual`
  - `update_progress(objective_id, kr_id, current_value)` — numeric or qualitative
  - `get_at_risk()` — objectives with <50% completion inside the last 20% of their horizon
- `data/strategy.db` table: `objectives`, `key_results`, `progress_log`
- Inject `StrategyEngine.get_active_plan()` into Brain's system prompt (alongside context block)
- New tool `strategic_planning` registered in `tools/registry.py`
  - Actions: `set_objective`, `update_kr`, `list_objectives`, `get_at_risk`, `generate_weekly_review`
- CLI trigger: `"strategic review"` / `"update OKR"` / `"what are our goals"`
- API endpoint: `GET /strategy/objectives`, `POST /strategy/objective`, `PUT /strategy/kr`

**Why first:** Every layer above this (Goal Engine, ROI Tracker, Governance) depends on knowing what the business is optimizing for.

---

### PR-11 — Goal Engine
**Branch:** `feature/goal-engine`

A dedicated goal-tracking system separate from the OKR layer. Where Strategy is high-altitude and quarterly, Goals are weekly, personal, and actionable. Jarvis should be able to accept a goal, break it into milestones, track completion, and proactively surface blocked goals.

**Deliverables:**
- `core/goals.py` — `GoalEngine`
  - `add_goal(title, description, deadline, linked_objective_id)` — links to Strategy layer
  - `add_milestone(goal_id, title, due_date)`
  - `complete_milestone(milestone_id)`
  - `get_active_goals()` — with milestone completion %
  - `get_blocked()` — goals with overdue milestones
- SQLite tables: `goals`, `milestones`
- Anticipator hook: surface blocked goals as HIGH alerts every morning
- Brain injection: active goals summary in context block (replace/extend `pending_task_count`)
- Tool: `goal_manager` with actions `add`, `update`, `complete`, `list`, `get_blocked`
- CLI: `"add goal"` / `"what are my active goals"` / `"mark milestone done"`
- API: `GET /goals`, `POST /goals`, `PUT /goals/{id}/milestone`

---

### PR-12 — Clarification Memory
**Branch:** `feature/clarification-memory`

Jarvis often makes assumptions. This layer tracks every assumption he makes, stores it, and when a clarification is given later, updates his model permanently. Eliminates repeating the same questions.

**Deliverables:**
- `core/clarifications.py` — `ClarificationMemory`
  - `add_assumption(context, assumption, confidence)` — logged when Jarvis assumes something
  - `add_clarification(trigger, clarification, scope)` — scope: `session | permanent`
  - `get_relevant(context_text)` — retrieves clarifications relevant to current context (fuzzy match)
  - `list_open_assumptions()` — assumptions never confirmed or denied
- SQLite tables: `assumptions`, `clarifications`
- Brain integration: inject relevant clarifications into system prompt before each call
- Auto-extraction: after each Brain response, detect assumption language ("I'll assume...", "I'm treating... as...") and store
- API: `GET /memory/assumptions`, `POST /memory/clarification`
- CLI: `"what assumptions have you made"` / `"remember that X means Y"`

---

### PR-13 — Business Command Center
**Branch:** `feature/business-command-center`

A unified dashboard and command interface for the business. Aggregates KPIs, revenue, burn, team status, and key metrics into a single prompt-ready snapshot Jarvis injects into context.

**Deliverables:**
- `core/command_center.py` — `BusinessCommandCenter`
  - `set_metric(name, value, unit, source, timestamp)` — ingest any metric
  - `get_dashboard()` — returns structured snapshot of all live metrics
  - `add_kpi(name, target, current, direction)` — direction: `up | down | stable`
  - `get_off_track_kpis()` — KPIs >20% away from target
  - `generate_brief()` — Claude-powered one-paragraph business status
- SQLite: `metrics`, `kpis`
- Inject `generate_brief()` snapshot into context (cached 5 min, not 60s)
- Extends existing `tools/business.py` — add `dashboard`, `set_metric`, `kpi_status` actions
- API: `GET /business/dashboard`, `POST /business/metric`, `GET /business/kpis`
- Anticipator: surface off-track KPIs as HIGH alerts in morning brief
- CLI: `"business status"` / `"update metric"` / `"how are we tracking"`

---

### PR-14 — Knowledge Graph
**Branch:** `feature/knowledge-graph`

Jarvis accumulates facts, but they live in flat tables. This layer builds a lightweight named-entity graph: people, companies, projects, concepts, and their relationships. Enables `"how does X relate to Y?"` and richer context injection.

**Deliverables:**
- `core/knowledge.py` — `KnowledgeGraph`
  - `add_entity(name, type, attributes)` — types: `person | company | project | concept | product`
  - `add_relation(from_entity, relation, to_entity, metadata)` — e.g. `("Acme", "is_client_of", "Us")`
  - `query(entity_name)` — returns entity + all relations + neighbor summaries
  - `find_path(entity_a, entity_b)` — shortest connection between two entities
  - `get_context_for(text)` — extract mentioned entities from text, return graph context
- SQLite: `entities`, `relations`
- Brain integration: `get_context_for(user_input)` injected into system prompt
- Auto-extraction: after each response, Claude extracts entities + relations and stores them (background task)
- Tool: `knowledge_graph` with actions `add_entity`, `add_relation`, `query`, `find_path`
- API: `GET /knowledge/{entity}`, `POST /knowledge/entity`, `POST /knowledge/relation`
- CLI: `"what do you know about X"` / `"how does X relate to Y"`

---

### PR-15 — Agent Hierarchy
**Branch:** `feature/agent-hierarchy`

Jarvis is one agent today. This layer lets him spin up specialized sub-agents for complex tasks: a Research Agent, a Writing Agent, a Code Agent, a Finance Agent. Each sub-agent has a scoped tool set, focused system prompt, and reports back to the Orchestrator (Brain).

**Deliverables:**
- `agents/base_agent.py` — `BaseAgent` with `run(goal) -> AsyncIterator[str]`, scoped tool registry, focused system prompt
- `agents/research_agent.py` — tools: web_search, research, notes, knowledge_graph
- `agents/writing_agent.py` — tools: notes, code_assistant (pr_description/adr), research
- `agents/code_agent.py` — tools: github, git_ops, code_assistant, architect
- `agents/finance_agent.py` — tools: finance_read, business (dashboard/metrics), web_search
- `agents/agent_manager.py` — `AgentManager`: routes goals to the right agent, spawns sub-tasks, aggregates results
- Brain integration: when a goal is complex/multi-domain, Brain delegates to AgentManager instead of running Executor directly
- Tool: `spawn_agent` with args `agent_type`, `goal` — triggers sub-agent and streams output
- API: `POST /agents/spawn`, `GET /agents/active`
- CLI: `"research X using the research agent"` / `"spawn code agent to..."`

---

### PR-16 — Governance Engine
**Branch:** `feature/governance-engine`

Jarvis takes autonomous actions. This layer makes every consequential decision auditable, reversible where possible, and policy-bound. Adds policy rules, an audit log, rollback support, and a human-in-the-loop escalation ladder.

**Deliverables:**
- `core/governance.py` — `GovernanceEngine`
  - `evaluate(action_type, args, context)` — returns `ALLOW | REQUIRE_CONFIRMATION | DENY` based on policies
  - `add_policy(name, rule, action)` — rule is a Python expression evaluated against action context
  - `log_action(tool, args, result, approved_by)` — immutable audit log
  - `get_audit_log(hours, tool_filter)` — queryable audit trail
  - `rollback(action_id)` — triggers reverse action if tool supports it
- SQLite: `governance_policies`, `audit_log`
- Replaces/extends the current `ToolSafety` framework — policies are now data, not code
- All tool executions routed through `GovernanceEngine.evaluate()` before running
- API: `GET /governance/audit`, `GET /governance/policies`, `POST /governance/policy`
- CLI: `"show audit log"` / `"add policy"` / `"rollback last action"`

---

### PR-17 — ROI Tracker
**Branch:** `feature/roi-tracker`

Every action Jarvis takes should have a measurable return. This layer tracks time saved, revenue influenced, risk avoided, and cost incurred per Jarvis action category. Gives a running ROI dashboard.

**Deliverables:**
- `core/roi.py` — `ROITracker`
  - `log_outcome(action_category, outcome_type, value, unit, notes)` — outcome_type: `time_saved | revenue | cost | risk_avoided`
  - `get_summary(days)` — aggregate ROI by category
  - `generate_report()` — Claude-written narrative ROI report
  - Auto-log: after each executor/orchestrator run, estimate time saved based on task complexity
- SQLite: `roi_outcomes`
- Business Command Center integration: ROI summary injected into dashboard brief
- Tool: `roi_tracker` with actions `log_outcome`, `summary`, `report`
- API: `GET /roi/summary`, `POST /roi/outcome`, `GET /roi/report`
- CLI: `"what's my ROI from Jarvis"` / `"log outcome"` / `"generate ROI report"`

---

### PR-18 — Research Pipeline
**Branch:** `feature/research-pipeline`

Today's `research` tool is a single-shot call. This layer adds a persistent, scheduled, multi-source research pipeline: standing research briefs (topics Jarvis monitors weekly), deep-dive reports on demand, and a synthesis layer that connects findings to the Knowledge Graph and Strategy layer.

**Deliverables:**
- `agents/researcher.py` — `ResearchPipeline`
  - `add_standing_brief(topic, sources, cadence)` — cadence: `daily | weekly | monthly`
  - `run_brief(topic_id)` — search + synthesize + store + diff against last result
  - `deep_dive(question, depth)` — multi-hop research: search → extract → follow citations → synthesize
  - `get_briefs()` — all standing briefs with last-run timestamp and summary
  - Stores findings in `notes` + extracts entities into `knowledge_graph`
- SQLite: `research_briefs`, `research_results`
- Anticipator: run due standing briefs as a background check (daily/weekly cadence)
- Tool: `research_pipeline` with actions `add_brief`, `run_brief`, `deep_dive`, `list_briefs`
- API: `GET /research/briefs`, `POST /research/brief`, `POST /research/deep-dive`
- CLI: `"add research brief on X"` / `"run research brief"` / `"deep dive on Y"`

---

### PR-19 — Observability Stack
**Branch:** `feature/observability-stack`

Jarvis has no introspection layer. This gives him (and you) full visibility into what he's doing, how long things take, what fails, and why. Structured logging, performance tracing, error budgets, and a health dashboard.

**Deliverables:**
- `core/observability.py` — `ObservabilityStack`
  - Structured JSON logging for every Brain call (model, tokens, latency, tool calls)
  - Structured JSON logging for every tool execution (tool, action, latency, success/fail)
  - `get_error_rate(hours, tool_filter)` — error % by tool
  - `get_latency_percentiles(hours)` — p50/p95/p99 for Brain + each tool
  - `get_health_dashboard()` — snapshot: uptime, error rates, slowest tools, last 10 failures
  - Alert if error rate >10% in any 1-hour window → URGENT anticipator alert
- SQLite: `trace_log` (all calls), `error_log` (failures only)
- Wrap `Brain._stream` and `ToolBase.run` with automatic tracing decorators
- API: `GET /observability/dashboard`, `GET /observability/traces`, `GET /observability/errors`
- CLI: `"system health"` / `"show error log"` / `"how fast is Jarvis"`
- Export: `GET /observability/traces.json` for ingestion into external tools (Grafana, Datadog)

---

## Backlog (future, unscheduled)

- Mobile app (React Native) consuming the Remote API
- Voice cloning / custom wake word training
- Multi-user support (separate memory spaces per user)
- Plugin marketplace (third-party tool registration)
- Calendar write (create events, schedule meetings)
- Email send / draft (with approval gate)
- Slack / Teams integration
- Long-term project memory with auto-archiving
- Fine-tuned Jarvis persona model

---

## Rules for Claude / Codex

1. **Always read this file before writing a single line of code.** The context in Completed tells you what already exists — do not re-implement it.
2. **One PR per item.** Do not bundle two Upcoming items into one PR.
3. **Mark the item complete the moment the PR merges** — update `[x]`, add the PR link and merge date, move it to Completed.
4. **Follow existing patterns:** `ToolBase` for tools, `AsyncIterator[str]` for streaming, SQLite via `core/memory.py`, FastAPI endpoints in `api/server.py`, CLI triggers in `main.py`.
5. **No orphan code.** Every new module must be wired into the registry, Brain context injection, main.py command routing, and the API — or explicitly noted why not.
6. **Update this file as part of every PR** — the roadmap is a living document, not a snapshot.
