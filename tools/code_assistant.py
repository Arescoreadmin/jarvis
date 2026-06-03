"""
Advanced coding intelligence tool.

Uses Claude to perform deep code analysis with context-awareness:
  - Security vulnerability scanning (OWASP-aligned)
  - Architecture review with pattern recognition
  - Code complexity and tech-debt scoring
  - Refactoring recommendations
  - Test generation
  - PR description drafting
  - Performance profiling guidance

JARVIS knows your active projects, recent files, and coding patterns
(via dev_context + distiller behavioral facts), making reviews
progressively more accurate over time.
"""
import os
from tools.registry import ToolBase, ToolSafety
import anthropic


REVIEW_SYSTEM = """\
You are an elite software engineer and security researcher performing a code review.
Be direct, specific, and actionable. No filler. No praise unless there's something
genuinely notable. Lead with the most critical finding.

For each issue:
  - State exactly what's wrong and why it matters
  - Show the fix (diff or replacement snippet)
  - Rate: CRITICAL / HIGH / MEDIUM / LOW

For architecture reviews, assess:
  - Coupling and cohesion
  - Scalability bottlenecks
  - Missing abstractions or leaky ones
  - Security surface area
"""

REFACTOR_SYSTEM = """\
You are a senior engineer focused on code quality and simplicity.
Identify: duplication, over-engineering, poor naming, unclear intent, missed stdlib/library usage.
Show the improved version. Explain the trade-off in one sentence per change.
"""

TEST_SYSTEM = """\
You are a TDD practitioner writing production-quality tests.
Cover: happy path, edge cases, error conditions, boundary values.
Use the same language/framework as the input code.
Tests must be runnable — no placeholders.
"""

ADR_SYSTEM = """\
You are a technical writer producing Architecture Decision Records (ADRs).
Format:
  # ADR-{number}: {title}
  Status: Proposed | Accepted | Deprecated | Superseded
  Date: {date}
  ## Context
  ## Decision
  ## Consequences (positive + negative)
  ## Alternatives Considered
Be concise. An ADR should fit on one page.
"""


class CodeAssistantTool(ToolBase):
    name = "code_assistant"
    description = (
        "Advanced code analysis: security review, architecture assessment, "
        "refactoring suggestions, test generation, PR descriptions, "
        "and Architecture Decision Records (ADRs). "
        "Provide code as a string or describe what to analyze."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["review", "security", "refactor", "tests", "pr_description", "adr", "complexity"],
                "description": "Type of analysis to perform",
            },
            "code": {
                "type": "string",
                "description": "Code to analyze",
            },
            "language": {
                "type": "string",
                "description": "Programming language (inferred if not provided)",
            },
            "context": {
                "type": "string",
                "description": "Additional context: what this code does, known constraints, etc.",
            },
            "focus": {
                "type": "string",
                "description": "For review: specific concern to focus on (e.g. 'injection attacks', 'async safety')",
            },
            "diff": {
                "type": "string",
                "description": "For pr_description: the git diff or summary of changes",
            },
            "decision": {
                "type": "string",
                "description": "For adr: the architectural decision being recorded",
            },
            "alternatives": {
                "type": "string",
                "description": "For adr: alternatives that were considered",
            },
        },
    }

    action_policies = {
        "review": ToolSafety(action_type="analysis", risk_level="low"),
        "security": ToolSafety(action_type="analysis", risk_level="low"),
        "refactor": ToolSafety(action_type="analysis", risk_level="low"),
        "tests": ToolSafety(action_type="generate", risk_level="low"),
        "pr_description": ToolSafety(action_type="generate", risk_level="low"),
        "adr": ToolSafety(action_type="generate", risk_level="low"),
        "complexity": ToolSafety(action_type="analysis", risk_level="low"),
    }

    def __init__(self):
        self._client = anthropic.AsyncAnthropic()

    async def run(
        self,
        action: str,
        code: str = "",
        language: str = "",
        context: str = "",
        focus: str = "",
        diff: str = "",
        decision: str = "",
        alternatives: str = "",
    ) -> str:
        lang_hint = f" ({language})" if language else ""
        ctx_hint = f"\n\nContext: {context}" if context else ""

        if action == "review":
            focus_hint = f"\n\nFocus specifically on: {focus}" if focus else ""
            prompt = f"Review this code{lang_hint}:{ctx_hint}{focus_hint}\n\n```\n{code}\n```"
            return await self._ask(REVIEW_SYSTEM, prompt, max_tokens=2000)

        elif action == "security":
            prompt = (
                f"Perform a security audit of this code{lang_hint}.{ctx_hint}\n"
                "Check for: injection, auth bypass, data exposure, insecure deserialization, "
                "broken access control, SSRF, path traversal, and any language-specific vulnerabilities.\n\n"
                f"```\n{code}\n```"
            )
            return await self._ask(REVIEW_SYSTEM, prompt, max_tokens=2000)

        elif action == "refactor":
            prompt = f"Refactor this code{lang_hint} for clarity, simplicity, and maintainability:{ctx_hint}\n\n```\n{code}\n```"
            return await self._ask(REFACTOR_SYSTEM, prompt, max_tokens=2000)

        elif action == "tests":
            prompt = f"Write comprehensive tests for this code{lang_hint}:{ctx_hint}\n\n```\n{code}\n```"
            return await self._ask(TEST_SYSTEM, prompt, max_tokens=2000)

        elif action == "pr_description":
            content = diff or code
            prompt = (
                f"Write a clear, useful PR description for these changes:{ctx_hint}\n\n"
                f"```\n{content}\n```\n\n"
                "Include: summary (bullet points), what changed and why, "
                "test plan checklist, any breaking changes or migration notes."
            )
            return await self._ask(REVIEW_SYSTEM, prompt, max_tokens=1000)

        elif action == "adr":
            from datetime import date
            prompt = (
                f"Write an ADR for this decision:\n"
                f"Decision: {decision}\n"
                f"Context: {context}\n"
                f"Alternatives considered: {alternatives or 'not specified'}\n"
                f"Date: {date.today().isoformat()}"
            )
            return await self._ask(ADR_SYSTEM, prompt, max_tokens=800)

        elif action == "complexity":
            prompt = (
                f"Analyze the complexity and tech debt of this code{lang_hint}:{ctx_hint}\n\n"
                f"```\n{code}\n```\n\n"
                "Assess: cyclomatic complexity, coupling, cohesion, test surface, "
                "maintenance burden. Give a 1-10 debt score and top 3 improvement priorities."
            )
            return await self._ask(REVIEW_SYSTEM, prompt, max_tokens=1000)

        return f"Unknown action: {action}"

    async def _ask(self, system: str, prompt: str, max_tokens: int = 1500) -> str:
        resp = await self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
