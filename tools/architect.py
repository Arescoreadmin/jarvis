"""
System architecture intelligence tool.

Designs systems, generates diagrams, compares technology stacks,
assesses scalability, and plans migrations. All output uses Claude
with architecture-specialist prompting.

Diagram output uses Mermaid syntax — rendered automatically in the
JARVIS web UI and compatible with GitHub, Notion, and most doc tools.
"""
from tools.registry import ToolBase, ToolSafety
import anthropic


DESIGN_SYSTEM = """\
You are a principal architect with deep experience in distributed systems,
cloud infrastructure, and product engineering. Your designs are practical,
not academic — optimized for a small team shipping fast while staying scalable.

For every design:
  1. Start with a Mermaid diagram (graph TD or sequenceDiagram)
  2. List the key components and their responsibilities
  3. Call out the 2-3 biggest architectural risks
  4. State what NOT to build yet (scope discipline)
  5. Estimate rough infra cost at the stated scale

Be opinionated. Don't present 5 options and let them pick — recommend one.
"""

COMPARE_SYSTEM = """\
You are a senior engineer evaluating technology choices under real-world constraints.
Compare options across: performance, operational complexity, ecosystem maturity,
team learning curve, cost, and lock-in risk. End with a clear recommendation.
No hedge answers. Pick one and defend it.
"""

SCALABILITY_SYSTEM = """\
You are a systems reliability engineer assessing scalability.
Identify: single points of failure, bottlenecks, stateful components that won't scale horizontally,
missing caching layers, N+1 query patterns, synchronous chains that should be async.
Give concrete numbers where possible (e.g., "this hits PostgreSQL 1000x/s at 10k users").
"""

MIGRATE_SYSTEM = """\
You are a senior architect planning a system migration.
The plan must be: incremental (no big-bang cutover), safe (each phase independently rollback-able),
and measurable (each phase has a clear success metric).
Include: pre-migration prep, phase-by-phase breakdown, data migration strategy, rollback plan.
"""


class ArchitectTool(ToolBase):
    name = "architect"
    description = (
        "System architecture intelligence: design systems from requirements, "
        "generate Mermaid diagrams, compare technology options, assess scalability, "
        "plan migrations, and produce Architecture Decision Records. "
        "Output includes diagrams renderable in the JARVIS UI."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["design", "diagram", "compare", "scalability", "migrate", "review"],
                "description": "Architecture operation to perform",
            },
            "description": {
                "type": "string",
                "description": "System or feature description",
            },
            "requirements": {
                "type": "string",
                "description": "Functional and non-functional requirements",
            },
            "scale": {
                "type": "string",
                "description": "Expected scale (e.g. '10k DAU', '1M requests/day')",
            },
            "constraints": {
                "type": "string",
                "description": "Team size, budget, tech stack constraints",
            },
            "option_a": {
                "type": "string",
                "description": "First technology/approach for comparison",
            },
            "option_b": {
                "type": "string",
                "description": "Second technology/approach for comparison",
            },
            "criteria": {
                "type": "string",
                "description": "Evaluation criteria for comparison",
            },
            "current_arch": {
                "type": "string",
                "description": "Description of current system for scalability/migration analysis",
            },
            "target_arch": {
                "type": "string",
                "description": "Target system description for migration planning",
            },
        },
    }

    action_policies = {a: ToolSafety(action_type="analysis", risk_level="low")
                       for a in ["design", "diagram", "compare", "scalability", "migrate", "review"]}

    def __init__(self):
        self._client = anthropic.AsyncAnthropic()

    async def run(
        self,
        action: str,
        description: str = "",
        requirements: str = "",
        scale: str = "",
        constraints: str = "",
        option_a: str = "",
        option_b: str = "",
        criteria: str = "",
        current_arch: str = "",
        target_arch: str = "",
    ) -> str:
        if action == "design":
            prompt = (
                f"Design a system for: {description}\n\n"
                f"Requirements: {requirements or 'not specified'}\n"
                f"Scale: {scale or 'early-stage startup'}\n"
                f"Constraints: {constraints or 'small team, move fast'}\n\n"
                "Include a Mermaid diagram as the first output block."
            )
            return await self._ask(DESIGN_SYSTEM, prompt, 2500)

        elif action == "diagram":
            prompt = (
                f"Generate a Mermaid diagram for: {description}\n"
                f"Components/entities: {requirements}\n"
                "Output ONLY the Mermaid code block, no prose."
            )
            return await self._ask(DESIGN_SYSTEM, prompt, 800)

        elif action == "compare":
            prompt = (
                f"Compare: {option_a} vs {option_b}\n"
                f"Use case: {description}\n"
                f"Evaluation criteria: {criteria or 'performance, ops complexity, ecosystem, cost'}\n"
                f"Constraints: {constraints or 'small team'}"
            )
            return await self._ask(COMPARE_SYSTEM, prompt, 1500)

        elif action == "scalability":
            prompt = (
                f"Assess scalability of:\n{current_arch or description}\n\n"
                f"Current/expected scale: {scale or 'unknown'}\n"
                f"Requirements: {requirements}"
            )
            return await self._ask(SCALABILITY_SYSTEM, prompt, 1500)

        elif action == "migrate":
            prompt = (
                f"Plan migration from:\n{current_arch}\n\n"
                f"To:\n{target_arch}\n\n"
                f"Constraints: {constraints or 'zero downtime preferred'}\n"
                f"Scale: {scale}"
            )
            return await self._ask(MIGRATE_SYSTEM, prompt, 2000)

        elif action == "review":
            prompt = (
                f"Architecture review of:\n{description or current_arch}\n\n"
                f"Requirements: {requirements}\n"
                f"Scale: {scale}"
            )
            return await self._ask(DESIGN_SYSTEM, prompt, 2000)

        return f"Unknown action: {action}"

    async def _ask(self, system: str, prompt: str, max_tokens: int = 1500) -> str:
        resp = await self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
