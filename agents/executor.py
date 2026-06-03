"""
Autonomous multi-step executor.

Takes a high-level goal, generates a structured execution plan using Claude,
then executes each step — respecting dependencies, handling confirmations,
and streaming status updates throughout.

Usage:
    executor = Executor(memory, modes, context, registry)
    async for update in executor.run("Research our top 3 competitors and update the notes"):
        print(update, end="", flush=True)

Step dependency model:
    Steps can declare depends_on: ["step_id", ...].
    Results are available to dependent steps via {{step_id}} placeholders in args.
    Independent steps with no shared dependencies run sequentially in plan order.
    Dependent steps wait for all their dependencies to resolve before executing.
"""
import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import anthropic


PLAN_TOOL = {
    "name": "create_execution_plan",
    "description": "Create a minimal, ordered execution plan for a user goal using available tools.",
    "input_schema": {
        "type": "object",
        "required": ["plan_summary", "safe_to_autorun", "steps"],
        "properties": {
            "plan_summary": {
                "type": "string",
                "description": "One sentence: what this plan accomplishes.",
            },
            "safe_to_autorun": {
                "type": "boolean",
                "description": (
                    "True if all steps are read-only or trivially reversible. "
                    "False if any step sends external communications, modifies finances, "
                    "controls physical devices, or deletes data."
                ),
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "description", "tool", "args"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Short identifier, e.g. '1', '2a', 'search'.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Plain-English description of what this step does.",
                        },
                        "tool": {
                            "type": "string",
                            "description": "Exact tool name from the available tools list.",
                        },
                        "args": {
                            "type": "object",
                            "description": (
                                "Arguments for the tool. Use {{step_id}} in string values "
                                "to reference the result of a previous step."
                            ),
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "IDs of steps that must complete before this one.",
                        },
                    },
                },
            },
        },
    },
}


@dataclass
class Step:
    id: str
    description: str
    tool: str
    args: dict
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"          # pending | running | done | failed | skipped | awaiting_confirm
    result: Optional[str] = None
    confirmation_id: Optional[str] = None


@dataclass
class ExecutionPlan:
    goal: str
    steps: list[Step]
    safe_to_autorun: bool
    plan_summary: str


class Executor:
    MODEL = "claude-sonnet-4-6"

    def __init__(self, memory, mode_manager, context_aggregator, tool_registry):
        self._memory = memory
        self._modes = mode_manager
        self._context = context_aggregator
        self._tools = tool_registry
        self._client = anthropic.AsyncAnthropic()

    async def run(self, goal: str) -> AsyncIterator[str]:
        yield f"Goal: {goal}\n"

        try:
            plan = await self._plan(goal)
        except Exception as e:
            yield f"Could not generate plan: {e}\n"
            return

        yield f"\n{plan.plan_summary}\n"
        yield f"Steps ({len(plan.steps)}):\n"
        for i, step in enumerate(plan.steps, 1):
            yield f"  {i}. {step.description}\n"
        yield "\n"

        results: dict[str, str] = {}
        remaining = list(plan.steps)

        while remaining:
            ready = [
                s for s in remaining
                if s.status == "pending"
                and all(
                    results.get(dep) is not None
                    for dep in s.depends_on
                    if self._dep_succeeded(dep, plan.steps)
                )
            ]

            if not ready:
                for s in remaining:
                    if s.status == "pending":
                        s.status = "skipped"
                        yield f"  ⟳ Skipped: {s.description} (dependency failed)\n"
                break

            for step in ready:
                remaining.remove(step)
                async for chunk in self._execute_step(step, results):
                    yield chunk
                results[step.id] = step.result or ""

        done = sum(1 for s in plan.steps if s.status == "done")
        waiting = sum(1 for s in plan.steps if s.status == "awaiting_confirm")
        failed = sum(1 for s in plan.steps if s.status == "failed")

        yield "\n"
        yield f"Completed {done}/{len(plan.steps)}"
        if waiting:
            yield f"  |  {waiting} awaiting confirmation"
        if failed:
            yield f"  |  {failed} failed"
        yield "\n"

        self._memory.add_episode(
            "assistant",
            f"Executed: {goal}. {done}/{len(plan.steps)} steps done.",
        )

    async def _execute_step(
        self, step: Step, results: dict[str, str]
    ) -> AsyncIterator[str]:
        tool = self._tools.get(step.tool)
        if not tool:
            step.status = "failed"
            step.result = f"Tool '{step.tool}' not available"
            yield f"  ✗ {step.description} — tool not found\n"
            return

        resolved_args = self._inject_results(step.args, results)

        safety = tool.safety_for(resolved_args)
        if safety.requires_confirmation:
            action_id = self._memory.add_pending_action(
                tool_name=step.tool,
                args=resolved_args,
                action_type=safety.action_type,
                description=step.description,
                reason=safety.reason,
            )
            step.status = "awaiting_confirm"
            step.confirmation_id = action_id
            yield f"  ⏸ {step.description}\n"
            yield f"     Needs approval — ID: {action_id[:8]}\n"
            yield f"     Approve at /actions/pending or in the UI\n"
            return

        step.status = "running"
        yield f"  → {step.description}…\n"

        try:
            result = await tool.run(**resolved_args)
            step.status = "done"
            step.result = str(result)
            preview = step.result[:120].replace("\n", " ")
            yield f"  ✓ {step.description}"
            if preview:
                yield f"\n     {preview}"
            yield "\n"
        except Exception as e:
            step.status = "failed"
            step.result = str(e)
            yield f"  ✗ {step.description}: {e}\n"

    async def _plan(self, goal: str) -> ExecutionPlan:
        context = await self._context.get()
        available = ", ".join(t.name for t in self._tools.all())

        prompt = (
            f"Goal: {goal}\n\n"
            f"Available tools: {available}\n\n"
            f"Current context:\n{context.to_prompt_block()}\n\n"
            "Rules:\n"
            "- Only use tools from the available list\n"
            "- Keep the plan minimal — no unnecessary steps\n"
            "- Use depends_on for steps that need prior results\n"
            "- Use {{step_id}} in args to reference a prior step's result\n"
            "- Set safe_to_autorun=false if any step sends messages, modifies "
            "physical systems, or has irreversible external effects\n"
        )

        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=1500,
            tools=[PLAN_TOOL],
            tool_choice={"type": "tool", "name": "create_execution_plan"},
            messages=[{"role": "user", "content": prompt}],
        )

        for block in resp.content:
            if block.type == "tool_use" and block.name == "create_execution_plan":
                data = block.input
                steps = [
                    Step(
                        id=str(s.get("id", i)),
                        description=s.get("description", ""),
                        tool=s.get("tool", ""),
                        args=s.get("args", {}),
                        depends_on=s.get("depends_on", []),
                    )
                    for i, s in enumerate(data.get("steps", []), 1)
                ]
                return ExecutionPlan(
                    goal=goal,
                    steps=steps,
                    safe_to_autorun=data.get("safe_to_autorun", False),
                    plan_summary=data.get("plan_summary", ""),
                )

        raise ValueError("Planner returned no valid plan")

    def _inject_results(self, args: dict, results: dict[str, str]) -> dict:
        """Replace {{step_id}} placeholders in string args with prior step results."""
        out = {}
        for key, value in args.items():
            if isinstance(value, str):
                for step_id, result in results.items():
                    value = value.replace(f"{{{{{step_id}}}}}", result or "")
            out[key] = value
        return out

    def _dep_succeeded(self, dep_id: str, steps: list[Step]) -> bool:
        for s in steps:
            if s.id == dep_id:
                return s.status not in ("failed", "skipped")
        return True
