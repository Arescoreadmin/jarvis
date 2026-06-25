"""
Claude API client with:
  - streaming responses
  - full tool use
  - automatic context injection
  - mode-aware system prompt
  - commitment extraction
  - behavioral logging
  - retry with exponential backoff
"""
import asyncio
import json
import re
from pathlib import Path
from typing import AsyncIterator, Optional

import anthropic

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "system.md"


def _extract_system_prompt(md_path: Path) -> str:
    """Pull only the content inside the first ```...``` block after '## SYSTEM PROMPT'."""
    text = md_path.read_text()
    match = re.search(r"## SYSTEM PROMPT\s*```(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


COMMITMENT_PATTERN = re.compile(
    r"(?:i(?:'ll| will)|let me|i(?:'ll| will) make sure to|telling|promise[sd]?)\b[^.]*?"
    r"(?:by|before|until|on|end of|eod|eow)\b[^.]*",
    re.IGNORECASE,
)


class Brain:
    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 2048
    MAX_RETRIES = 3

    def __init__(self, memory, mode_manager, context_aggregator, tool_registry):
        self._memory = memory
        self._modes = mode_manager
        self._context = context_aggregator
        self._tools = tool_registry
        self._client = anthropic.AsyncAnthropic()
        self._base_system = _extract_system_prompt(SYSTEM_PROMPT_PATH)

    def _build_system(self, context_snapshot, mention_names: list[str] | None = None) -> str:
        profile = self._memory.get_user_profile()
        name = profile.get("preferred_name") or profile.get("name") or "the user"
        mode_addendum = self._modes.profile.system_addendum
        context_block = context_snapshot.to_prompt_block()

        # Strategic plan block — injected so every response is goal-aligned
        strategy_block = ""
        try:
            from core.strategy import StrategyEngine
            strategy_block = StrategyEngine().get_active_plan()
        except Exception:
            pass

        # Active goals block
        goals_block = ""
        try:
            from core.goals import GoalEngine
            goals_block = GoalEngine().get_context_block()
        except Exception:
            pass

        procedures = self._memory.get_all_procedures()
        proc_block = ""
        if procedures:
            lines = ["USER SHORTCUTS (procedural memory):"]
            for p in procedures[:10]:
                lines.append(f'  "{p["trigger"]}" → {p["expansion"]}')
            proc_block = "\n".join(lines)

        # Behavioral facts extracted by distiller
        behavioral = []
        for key in ("prefers_short_answers", "communication_style", "risk_tolerance",
                    "work_style", "decision_speed", "top_concerns"):
            val = self._memory.get_semantic(f"profile/{key}")
            if val:
                behavioral.append(f"  {key}: {val}")
        behavioral_block = ("Behavioral profile:\n" + "\n".join(behavioral)) if behavioral else ""

        return "\n\n---\n\n".join(filter(None, [
            self._base_system,
            f"The user's name is {name}.",
            mode_addendum,
            context_block,
            strategy_block,
            goals_block,
            proc_block,
            behavioral_block,
        ]))

    async def respond(
        self,
        user_input: str,
        stream: bool = True,
        images: list[dict] | None = None,
    ) -> AsyncIterator[str] | str:
        """
        images: optional list of {"base64": "...", "media_type": "image/jpeg|png|gif|webp"}
        """
        context = await self._context.get()
        system = self._build_system(context)
        history = self._memory.get_session_history()
        self._memory.add_episode("user", user_input)

        if images:
            user_content: list = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["base64"],
                    },
                }
                for img in images
            ]
            user_content.append({"type": "text", "text": user_input})
            messages = history + [{"role": "user", "content": user_content}]
        else:
            messages = history + [{"role": "user", "content": user_input}]

        tool_schemas = self._tools.to_claude_schema()

        if stream:
            return self._stream(system, messages, tool_schemas, user_input)
        else:
            return await self._complete(system, messages, tool_schemas, user_input)

    async def _stream(
        self,
        system: str,
        messages: list,
        tool_schemas: list,
        original_input: str,
    ) -> AsyncIterator[str]:
        full_response = []
        tool_calls = []

        for attempt in range(self.MAX_RETRIES):
            try:
                kwargs = dict(
                    model=self.MODEL,
                    max_tokens=self.MAX_TOKENS,
                    system=system,
                    messages=messages,
                )
                if tool_schemas:
                    kwargs["tools"] = tool_schemas

                async with self._client.messages.stream(**kwargs) as stream:
                    current_tool_use = None

                    async for event in stream:
                        if event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                current_tool_use = {
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input_json": "",
                                }
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                chunk = event.delta.text
                                full_response.append(chunk)
                                yield chunk
                            elif event.delta.type == "input_json_delta" and current_tool_use:
                                current_tool_use["input_json"] += event.delta.partial_json
                        elif event.type == "content_block_stop":
                            if current_tool_use:
                                tool_calls.append(current_tool_use)
                                current_tool_use = None

                break

            except anthropic.RateLimitError:
                wait = 2 ** attempt
                await asyncio.sleep(wait)
            except anthropic.APIConnectionError:
                if attempt == self.MAX_RETRIES - 1:
                    yield "[JARVIS offline — connection failed]"
                    return
                await asyncio.sleep(2 ** attempt)

        response_text = "".join(full_response)
        if response_text:
            self._memory.add_episode("assistant", response_text)
            self._extract_and_store_commitments(original_input, response_text)

        for call in tool_calls:
            async for chunk in self._execute_tool_and_continue(
                call, system, messages, response_text
            ):
                yield chunk

    async def _complete(
        self,
        system: str,
        messages: list,
        tool_schemas: list,
        original_input: str,
    ) -> str:
        for attempt in range(self.MAX_RETRIES):
            try:
                kwargs = dict(
                    model=self.MODEL,
                    max_tokens=self.MAX_TOKENS,
                    system=system,
                    messages=messages,
                )
                if tool_schemas:
                    kwargs["tools"] = tool_schemas

                resp = await self._client.messages.create(**kwargs)
                text = "".join(
                    b.text for b in resp.content if hasattr(b, "text")
                )
                self._memory.add_episode("assistant", text)
                self._extract_and_store_commitments(original_input, text)
                return text

            except (anthropic.RateLimitError, anthropic.APIConnectionError):
                if attempt == self.MAX_RETRIES - 1:
                    return "[JARVIS offline — could not complete request]"
                await asyncio.sleep(2 ** attempt)

        return ""

    async def _execute_tool_and_continue(
        self,
        tool_call: dict,
        system: str,
        messages: list,
        prior_response: str,
    ) -> AsyncIterator[str]:
        name = tool_call["name"]
        try:
            args = json.loads(tool_call["input_json"]) if tool_call["input_json"] else {}
        except json.JSONDecodeError:
            args = {}

        tool = self._tools.get(name)
        if not tool:
            yield f"[Tool {name} not available]"
            return

        safety = tool.safety_for(args)
        if safety.requires_confirmation:
            action_id = self._memory.add_pending_action(
                tool_name=name,
                args=args,
                action_type=safety.action_type,
                risk_level=safety.risk_level,
                reason=safety.reason,
            )
            action = args.get("action", name)
            yield (
                f"Confirmation required [{action_id}] before I {action} via {name}. "
                f"Reason: {safety.reason}"
            )
            return

        try:
            result = await tool.run(**args)
        except Exception as e:
            result = f"Error: {e}"

        new_messages = messages + [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": prior_response},
                    {"type": "tool_use", "id": tool_call["id"], "name": name, "input": args},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": str(result),
                    }
                ],
            },
        ]

        async for chunk in self._stream(system, new_messages, self._tools.to_claude_schema(), ""):
            yield chunk

    def _extract_and_store_commitments(self, user_input: str, response: str) -> None:
        combined = f"{user_input} {response}"
        matches = COMMITMENT_PATTERN.findall(combined)
        for match in matches[:3]:
            text = match.strip()
            if len(text) > 20:
                self._memory.add_commitment(
                    made_to="[unspecified]",
                    description=text,
                    context=user_input[:200],
                    priority="medium",
                )

    async def inject_proactive(self, message: str, priority: str = "high") -> str:
        """Generate a proactive surface message from JARVIS."""
        context = await self._context.get()
        system = self._build_system(context)
        prompt = (
            f"[PROACTIVE SURFACE — {priority.upper()}]\n"
            f"Surface this to the user in JARVIS voice, one or two sentences max:\n{message}"
        )
        return await self._complete(system, [{"role": "user", "content": prompt}], [], "")
