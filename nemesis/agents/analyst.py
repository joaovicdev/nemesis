"""Analyst agent — processes raw executor output into structured, scored findings.

The Analyst is the gatekeeper between raw tool output and the Orchestrator.
It is responsible for:
  1. Parsing raw tool output into candidate findings
  2. Scoring confidence (0.0 – 1.0)
  3. Filtering likely false positives (DISMISSED before the Orchestrator sees them)
  4. Correlating new findings against existing ones in the ProjectContext
  5. Tagging CVE IDs, affected services, and severity

All LLM calls in this module use a compact, structured prompt optimized for
analysis rather than conversation — lower temperature, JSON output.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from nemesis.agents.executor import ExecutorResult
from nemesis.core.project import ProjectContext
from nemesis.db.models import Finding, FindingSeverity, FindingStatus


logger = logging.getLogger(__name__)

# Confidence thresholds
_AUTO_DISMISS_BELOW = 0.25   # findings below this are auto-dismissed as noise
_NEEDS_REVIEW_BELOW = 0.60   # findings below this are flagged for user review


class AnalystAgent:
    """
    Processes raw executor output into structured findings.

    Usage:
        analyst = AnalystAgent(context, llm_client)
        findings = await analyst.process(executor_result)
    """

    def __init__(self, context: ProjectContext, llm_client: object) -> None:
        """
        Args:
            context: The active ProjectContext — used for correlation.
            llm_client: LiteLLM client (to be wired in next milestone).
        """
        self._context = context
        self._llm = llm_client

    async def process(self, result: ExecutorResult) -> list[Finding]:
        """
        Entry point: given raw executor output, return a list of validated findings.

        Pipeline:
          1. Extract candidate findings from raw output (LLM call)
          2. Score confidence for each
          3. Dismiss noise below threshold
          4. Correlate with existing findings
          5. Return findings with status=UNVERIFIED (ready for Orchestrator)
        """
        logger.info(
            "[Analyst] Processing output from %s on %s (%d chars)",
            result.tool,
            result.target,
            len(result.stdout),
        )

        if not result.stdout.strip() and not result.stderr.strip():
            logger.debug("[Analyst] Empty output from %s — no findings.", result.tool)
            return []

        # Step 1: Extract candidates (placeholder — LLM call goes here)
        candidates = await self._extract_candidates(result)

        # Step 2 + 3: Score and filter
        findings: list[Finding] = []
        for candidate in candidates:
            confidence = candidate.get("confidence", 0.5)
            if confidence < _AUTO_DISMISS_BELOW:
                logger.debug(
                    "[Analyst] Auto-dismissed (confidence=%.2f): %s",
                    confidence,
                    candidate.get("title"),
                )
                continue

            finding = self._build_finding(candidate, result)

            # Step 4: Correlate
            self._correlate(finding)

            findings.append(finding)
            logger.info(
                "[Analyst] Finding: %s | %s | confidence=%.2f",
                finding.severity.value.upper(),
                finding.title,
                finding.confidence,
            )

        return findings

    async def _extract_candidates(
        self, result: ExecutorResult
    ) -> list[dict[str, object]]:
        """
        Use LLM to extract structured finding candidates from raw tool output.

        Returns a list of dicts with keys:
          title, description, severity, confidence, port, service, cve_ids, remediation

        PLACEHOLDER — LLM integration will be added in the next milestone.
        """
        # TODO: replace with actual LiteLLM call
        logger.debug(
            "[Analyst] _extract_candidates placeholder — LLM not yet wired"
        )
        return []

    def _build_finding(
        self, candidate: dict[str, object], result: ExecutorResult
    ) -> Finding:
        """Construct a Finding from a candidate dict and the executor result."""
        severity_raw = str(candidate.get("severity", "info")).lower()
        try:
            severity = FindingSeverity(severity_raw)
        except ValueError:
            severity = FindingSeverity.INFO

        return Finding(
            id=str(uuid.uuid4()),
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            title=str(candidate.get("title", "Unnamed finding")),
            description=str(candidate.get("description", "")),
            severity=severity,
            status=FindingStatus.UNVERIFIED,
            confidence=float(candidate.get("confidence", 0.5)),
            target=result.target,
            port=str(candidate.get("port", "")),
            service=str(candidate.get("service", "")),
            cve_ids=list(candidate.get("cve_ids", [])),  # type: ignore[arg-type]
            tool_source=result.tool,
            raw_evidence=result.stdout[:2000],  # cap to avoid bloating the DB
            remediation=str(candidate.get("remediation", "")),
            discovered_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

    def _correlate(self, finding: Finding) -> None:
        """
        Check for relationships between this finding and existing validated findings.
        Sets finding.related_finding_ids if correlations are found.

        Simple heuristic for now — same target + overlapping CVE IDs.
        The LLM-powered correlation pass will run separately.
        """
        for existing in self._context.get_validated_findings():
            if existing.id == finding.id:
                continue
            # Same target
            if existing.target == finding.target:
                # Overlapping CVEs
                if set(existing.cve_ids) & set(finding.cve_ids):
                    finding.related_finding_ids.append(existing.id)
                    logger.debug(
                        "[Analyst] Correlated %s with existing finding %s",
                        finding.title,
                        existing.title,
                    )
