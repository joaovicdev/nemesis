"""ProjectContext — runtime state of the active project, held by the Orchestrator."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from nemesis.db.models import (
    ControlMode,
    Finding,
    FindingStatus,
    Project,
    Session,
    SessionPhase,
)

logger = logging.getLogger(__name__)


def _target_matches(candidate: str, scope_entry: str) -> bool:
    """
    Check whether *candidate* falls within *scope_entry*.

    Handles four cases:
      1. Exact string match (hostname, IP, domain)
      2. Subdomain suffix match  (*.example.com)
      3. IP address within a CIDR range
      4. CIDR range within another CIDR range (subset)
    """
    candidate = candidate.strip().lower()
    scope_entry = scope_entry.strip().lower()

    if candidate == scope_entry:
        return True

    if candidate.endswith(f".{scope_entry}"):
        return True

    try:
        scope_net = ipaddress.ip_network(scope_entry, strict=False)
    except ValueError:
        return False

    try:
        candidate_addr = ipaddress.ip_address(candidate)
        return candidate_addr in scope_net
    except ValueError:
        pass

    try:
        candidate_net = ipaddress.ip_network(candidate, strict=False)
        return candidate_net.subnet_of(scope_net)
    except (ValueError, TypeError):
        pass

    return False


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
            "Session phase advanced",
            extra={
                "event": "project.phase_advanced",
                "project_id": self.project.id,
                "from_phase": self.session.phase.value,
                "to_phase": phase.value,
            },
        )
        self.session.phase = phase

    # ── Finding management ────────────────────────────────────────────────

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)
        logger.info(
            "Finding added to context",
            extra={
                "event": "project.finding_added",
                "finding_id": finding.id,
                "title": finding.title,
                "severity": finding.severity.value,
                "status": finding.status.value,
                "tool_source": finding.tool_source,
            },
        )

    def get_findings_by_status(self, status: FindingStatus) -> list[Finding]:
        return [f for f in self.findings if f.status == status]

    def get_validated_findings(self) -> list[Finding]:
        return self.get_findings_by_status(FindingStatus.VALIDATED)

    def get_critical_findings(self) -> list[Finding]:
        from nemesis.db.models import FindingSeverity

        return [
            f
            for f in self.findings
            if f.severity == FindingSeverity.CRITICAL and f.status not in (FindingStatus.DISMISSED,)
        ]

    # ── Scope validation ──────────────────────────────────────────────────

    def is_in_scope(self, target: str) -> bool:
        """
        Check whether a target string is within the project scope.

        Supports:
          - Exact hostname / IP match
          - Subdomain suffix match
          - IP address within a CIDR range (e.g. 192.168.1.5 in 192.168.1.0/24)
          - CIDR subnet within a CIDR range

        Out-of-scope entries are checked first and take priority over in-scope.
        """
        target = target.strip().lower()

        for oos in self.project.out_of_scope:
            if _target_matches(target, oos):
                logger.debug(
                    "Scope check: out of scope",
                    extra={
                        "event": "project.scope_checked",
                        "result": "out_of_scope",
                        "project_id": self.project.id,
                    },
                )
                return False

        for scope_entry in self.project.targets:
            if _target_matches(target, scope_entry):
                logger.debug(
                    "Scope check: in scope",
                    extra={
                        "event": "project.scope_checked",
                        "result": "in_scope",
                        "project_id": self.project.id,
                    },
                )
                return True

        logger.debug(
            "Scope check: not in targets",
            extra={
                "event": "project.scope_checked",
                "result": "out_of_scope",
                "project_id": self.project.id,
            },
        )
        return False

    def assert_in_scope(self, target: str) -> None:
        """Raise ValueError if target is out of scope."""
        if not self.is_in_scope(target):
            raise ValueError(
                f"Target '{target}' is outside the project scope. "
                f"Configured targets: {self.project.targets}. "
                f"Out-of-scope: {self.project.out_of_scope}."
            )

    # ── Destructive action gate ───────────────────────────────────────────

    def record_destructive_confirmation(self, action_id: str) -> None:
        """Mark that the user has confirmed a destructive action."""
        self._destructive_confirmed.add(action_id)
        logger.log(  # type: ignore[attr-defined]
            25,  # AUDIT level
            "Destructive action confirmed",
            extra={
                "event": "project.destructive_confirmed",
                "action_id": action_id,
                "project_id": self.project.id,
                "session_id": self.session.id,
                "confirmed_at": datetime.now(tz=UTC).isoformat(),
            },
        )

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

        if self.project.out_of_scope:
            lines.append(f"Out of scope: {', '.join(self.project.out_of_scope)}")

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

    # ── Activation event ──────────────────────────────────────────────────

    def log_activated(self) -> None:
        """Emit an observability event when this context becomes the active session."""
        logger.info(
            "Project context activated",
            extra={
                "event": "project.activated",
                "project_id": self.project.id,
                "session_id": self.session.id,
                "mode": self.project.mode.value,
                "phase": self.session.phase.value,
                "target_count": len(self.project.targets),
            },
        )

    # ── Control mode ──────────────────────────────────────────────────────

    @property
    def mode(self) -> ControlMode:
        return self.project.mode

    def set_mode(self, mode: ControlMode) -> None:
        self.project.mode = mode
