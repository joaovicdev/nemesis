"""ProjectContext — runtime state of the active project, held by the Orchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from nemesis.db.models import (
    ControlMode,
    Finding,
    FindingStatus,
    Project,
    Session,
    SessionPhase,
)


logger = logging.getLogger(__name__)


@dataclass
class ProjectContext:
    """
    In-memory state for the currently active engagement.

    This is the single source of truth the Orchestrator reads from during a session.
    It is backed by the SQLite database — all mutations should be persisted via the
    Database class after updating this context.
    """

    project: Project
    session: Session

    # Accumulated findings for this session (and loaded from previous sessions)
    findings: list[Finding] = field(default_factory=list)

    # Chat history (abbreviated, for LLM context window management)
    chat_summary: str = ""

    # Whether any destructive action has been confirmed this session
    _destructive_confirmed: set[str] = field(default_factory=set)

    # ── Phase management ──────────────────────────────────────────────────

    @property
    def current_phase(self) -> SessionPhase:
        return self.session.phase

    def advance_phase(self, phase: SessionPhase) -> None:
        logger.info(
            "Project %s: phase %s → %s",
            self.project.name,
            self.session.phase.value,
            phase.value,
        )
        self.session.phase = phase

    # ── Finding management ────────────────────────────────────────────────

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)
        logger.debug("Finding added: %s (%s)", finding.title, finding.severity.value)

    def get_findings_by_status(self, status: FindingStatus) -> list[Finding]:
        return [f for f in self.findings if f.status == status]

    def get_validated_findings(self) -> list[Finding]:
        return self.get_findings_by_status(FindingStatus.VALIDATED)

    def get_critical_findings(self) -> list[Finding]:
        from nemesis.db.models import FindingSeverity
        return [
            f for f in self.findings
            if f.severity == FindingSeverity.CRITICAL
            and f.status not in (FindingStatus.DISMISSED,)
        ]

    # ── Scope validation ──────────────────────────────────────────────────

    def is_in_scope(self, target: str) -> bool:
        """
        Check whether a target string is within the project scope.

        This is a basic check — a full implementation should handle CIDR ranges,
        subdomain matching, and wildcard entries.
        """
        target = target.strip().lower()
        for scope_target in self.project.targets:
            scope_target = scope_target.strip().lower()
            if target == scope_target:
                return True
            # Subdomain check (e.g. target=api.foo.com, scope=foo.com)
            if target.endswith(f".{scope_target}"):
                return True
        return False

    def assert_in_scope(self, target: str) -> None:
        """Raise ValueError if target is out of scope."""
        if not self.is_in_scope(target):
            raise ValueError(
                f"Target '{target}' is outside the project scope: {self.project.targets}"
            )

    # ── Destructive action gate ───────────────────────────────────────────

    def record_destructive_confirmation(self, action_id: str) -> None:
        """Mark that the user has confirmed a destructive action."""
        self._destructive_confirmed.add(action_id)

    def was_confirmed(self, action_id: str) -> bool:
        return action_id in self._destructive_confirmed

    # ── Context summary for LLM prompt injection ─────────────────────────

    def build_llm_context_summary(self) -> str:
        """
        Build a compact summary of project state to inject into LLM prompts.
        Keeps token usage predictable regardless of session length.
        """
        validated = self.get_validated_findings()
        critical = [f for f in validated if f.severity.value == "critical"]
        high = [f for f in validated if f.severity.value == "high"]

        lines: list[str] = [
            f"Project: {self.project.name}",
            f"Targets: {', '.join(self.project.targets)}",
            f"Phase: {self.session.phase.value}",
            f"Mode: {self.project.mode.value}",
        ]

        if self.project.context:
            lines.append(f"Engagement context: {self.project.context}")

        lines.append(
            f"Findings so far: {len(validated)} validated "
            f"({len(critical)} critical, {len(high)} high)"
        )

        if critical:
            lines.append("Critical findings:")
            for f in critical[:5]:  # cap at 5 to keep context compact
                lines.append(f"  - {f.title} [{f.target}:{f.port}]")

        if self.chat_summary:
            lines.append(f"Session summary: {self.chat_summary}")

        return "\n".join(lines)

    # ── Control mode ──────────────────────────────────────────────────────

    @property
    def mode(self) -> ControlMode:
        return self.project.mode

    def set_mode(self, mode: ControlMode) -> None:
        self.project.mode = mode
