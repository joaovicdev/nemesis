"""PlannerAgent — generates a structured multi-step attack plan via LLM."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from nemesis.agents.llm_client import LLMClient, LLMError
from nemesis.core.project import ProjectContext
from nemesis.db.models import AttackPlan, PlanStep, PlanStepStatus

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are NEMESIS PlannerAgent, an AI penetration testing strategist.
Your sole job is to produce a structured, multi-step attack plan for an authorized engagement.

Rules:
- Output valid JSON only — no markdown fences, no extra text outside the JSON object.
- Each step must have a unique id in the format "step-NNN" (zero-padded, starting at 001).
- depends_on must only reference ids of earlier steps in the same plan.
- required_tools must be a subset of: ["nmap", "whois", "dig", "gobuster", "nikto"].
- agent must be one of: "recon_agent", "scanning_agent", "enumeration_agent", "vulnerability_agent".
- Keep the plan focused: 3–7 steps covering the full engagement lifecycle.

Output schema (strict):
{
  "goal": "<concise description of what the plan achieves>",
  "steps": [
    {
      "id": "step-001",
      "name": "<short name>",
      "description": "<what this step does and why>",
      "required_tools": ["<tool>"],
      "depends_on": [],
      "agent": "<agent_name>",
      "args": {"target": "<primary target>"}
    }
  ]
}
"""

_USER_PROMPT_TEMPLATE = """\
Engagement context:
{context_summary}

Generate a full structured attack plan for the targets listed above.
Cover: OSINT/DNS recon → port/service scanning → web enumeration (if applicable) → \
vulnerability assessment.
Output JSON only.
"""

_DEFAULT_PLAN_STEPS: list[dict] = [
    {
        "id": "step-001",
        "name": "WHOIS Recon",
        "description": "Gather domain registration details and ASN/org info.",
        "required_tools": ["whois"],
        "depends_on": [],
        "agent": "recon_agent",
        "args": {},
    },
    {
        "id": "step-002",
        "name": "Port & Service Scan",
        "description": "Enumerate open ports and identify running services with version detection.",
        "required_tools": ["nmap"],
        "depends_on": ["step-001"],
        "agent": "scanning_agent",
        "args": {"extra_args": ["-sV", "-sC", "-T4"]},
    },
    {
        "id": "step-003",
        "name": "Web Directory Brute-Force",
        "description": "Discover hidden web paths and admin panels.",
        "required_tools": ["gobuster"],
        "depends_on": ["step-002"],
        "agent": "enumeration_agent",
        "args": {},
    },
]


class PlannerAgent:
    """
    Generates a structured AttackPlan for the current project via LLM.

    Falls back to a hardcoded 3-step default plan (whois → nmap → gobuster)
    when the LLM is unavailable or returns an invalid response.
    """

    def __init__(self, context: ProjectContext, llm: LLMClient) -> None:
        self._context = context
        self._llm = llm

    async def generate_plan(self, goal: str) -> AttackPlan:
        """
        Ask the LLM to produce a multi-step attack plan and return an AttackPlan.

        Args:
            goal: High-level objective for this engagement (used as plan.goal fallback).

        Returns:
            A validated AttackPlan with at least one PlanStep.
        """
        context_summary = self._context.build_llm_context_summary()
        prompt = _USER_PROMPT_TEMPLATE.format(context_summary=context_summary)

        try:
            raw = await self._llm.chat_json(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
            return self._parse_plan(raw, goal)

        except LLMError as exc:
            logger.warning(
                "PlannerAgent LLM call failed — using default plan",
                extra={
                    "event": "planner.llm_failed",
                    "error_type": type(exc).__name__,
                    "project_id": self._context.project.id,
                },
            )
            return self._default_plan(goal)

        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "PlannerAgent response parse error — using default plan",
                extra={
                    "event": "planner.parse_failed",
                    "error_type": type(exc).__name__,
                    "project_id": self._context.project.id,
                },
            )
            return self._default_plan(goal)

    # ── Internals ──────────────────────────────────────────────────────────

    def _parse_plan(self, raw: dict, fallback_goal: str) -> AttackPlan:
        """Validate and convert LLM JSON dict into an AttackPlan."""
        plan_goal = str(raw.get("goal", fallback_goal)).strip() or fallback_goal
        raw_steps: list[dict] = raw.get("steps", [])

        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("LLM plan has no steps")

        steps: list[PlanStep] = []
        seen_ids: set[str] = set()
        for item in raw_steps:
            step_id = str(item.get("id", "")).strip()
            if not step_id or step_id in seen_ids:
                continue
            seen_ids.add(step_id)
            try:
                steps.append(
                    PlanStep.model_validate({**item, "status": PlanStepStatus.PENDING})
                )
            except ValidationError:
                logger.warning("Skipping invalid plan step: %s", item)

        if not steps:
            raise ValueError("Parsed plan produced zero valid steps")

        logger.info(
            "PlannerAgent plan generated",
            extra={
                "event": "planner.plan_generated",
                "step_count": len(steps),
                "project_id": self._context.project.id,
                "session_id": self._context.session.id,
            },
        )

        return AttackPlan(
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            goal=plan_goal,
            steps=steps,
        )

    def _default_plan(self, goal: str) -> AttackPlan:
        """Return the hardcoded fallback plan with the first project target injected."""
        targets = self._context.project.targets
        first_target = targets[0] if targets else "unknown"

        steps: list[PlanStep] = []
        for raw in _DEFAULT_PLAN_STEPS:
            args = dict(raw["args"])
            args.setdefault("target", first_target)
            steps.append(
                PlanStep(
                    id=raw["id"],
                    name=raw["name"],
                    description=raw["description"],
                    required_tools=list(raw["required_tools"]),
                    depends_on=list(raw["depends_on"]),
                    agent=raw["agent"],
                    args=args,
                    status=PlanStepStatus.PENDING,
                )
            )

        logger.info(
            "PlannerAgent using default fallback plan",
            extra={
                "event": "planner.default_plan_used",
                "project_id": self._context.project.id,
                "session_id": self._context.session.id,
            },
        )

        return AttackPlan(
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            goal=goal,
            steps=steps,
        )
