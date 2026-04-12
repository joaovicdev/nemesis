"""Analyst agent — processes raw executor output into structured, scored findings.

The Analyst is the gatekeeper between raw tool output and the Orchestrator.
Pipeline per executor result:
  1. Extract candidate findings via LLM (JSON) — falls back to regex if LLM fails.
  2. Score confidence (0.0 – 1.0) and auto-dismiss noise below threshold.
  3. Correlate new findings against existing validated ones in ProjectContext.
  4. Return findings with status=UNVERIFIED, ready for Orchestrator promotion.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime

from nemesis.agents.executor import ExecutorResult
from nemesis.agents.llm_client import LLMClient, LLMError
from nemesis.core.project import ProjectContext
from nemesis.db.models import Finding, FindingSeverity, FindingStatus

logger = logging.getLogger(__name__)

_AUTO_DISMISS_BELOW = 0.25
_NEEDS_REVIEW_BELOW = 0.60

_RAW_OUTPUT_CAP = 4000

_ANALYST_SYSTEM = (
    "You are a penetration testing analyst. "
    "Extract security-relevant findings from raw tool output. "
    "Reply with valid JSON only — no markdown, no explanation outside the JSON."
)

_ANALYST_PROMPT = """\
Tool: {tool}
Target: {target}

Raw output (may be truncated):
---
{raw_output}
---

Extract every security-relevant finding from the output above.
For each finding include:
  - title: short descriptive name
  - description: what was found and why it matters
  - severity: "critical" | "high" | "medium" | "low" | "info"
  - confidence: float 0.0–1.0 (how certain you are this is a real finding)
  - port: port number as string, or "" if not applicable
  - service: service name (e.g. "ssh", "http"), or ""
  - cve_ids: list of CVE IDs if known, e.g. ["CVE-2021-1234"], or []
  - remediation: brief remediation advice, or ""

Reply with this exact JSON structure:
{{
  "findings": [
    {{
      "title": "...",
      "description": "...",
      "severity": "info",
      "confidence": 0.8,
      "port": "22",
      "service": "ssh",
      "cve_ids": [],
      "remediation": "..."
    }}
  ]
}}

If there are no meaningful findings, reply with: {{"findings": []}}
"""

# Nmap open-port regex: matches lines like "22/tcp   open  ssh"
_NMAP_OPEN_PORT_RE = re.compile(
    r"(\d{1,5})/(tcp|udp)\s+open\s+(\S+)(?:\s+(.+))?",
    re.IGNORECASE,
)

# Known high-risk services for severity escalation in the regex fallback
_HIGH_RISK_SERVICES = {"telnet", "ftp", "rsh", "rlogin", "rexec", "vnc", "rdp"}
_MEDIUM_RISK_SERVICES = {"ssh", "smtp", "pop3", "imap", "snmp", "nfs", "smb", "ldap"}


class AnalystAgent:
    """
    Processes raw executor output into structured findings.

    Usage:
        analyst = AnalystAgent(context, llm_client)
        findings = await analyst.process(executor_result)
    """

    def __init__(self, context: ProjectContext, llm_client: LLMClient) -> None:
        self._context = context
        self._llm = llm_client

    async def process(self, result: ExecutorResult) -> list[Finding]:
        """
        Entry point: given raw executor output, return a list of unverified findings.

        Tries LLM extraction first; falls back to regex heuristics if the LLM
        is unavailable or returns nothing useful.
        """
        output_bytes = len(result.stdout) + len(result.stderr)
        logger.info(
            "Analyst processing started",
            extra={
                "event": "analyst.processing_started",
                "tool": result.tool,
                "task_id": result.task_id,
                "output_bytes": output_bytes,
            },
        )

        if not result.stdout.strip() and not result.stderr.strip():
            logger.debug(
                "Empty tool output — no findings",
                extra={
                    "event": "analyst.empty_output",
                    "tool": result.tool,
                    "task_id": result.task_id,
                },
            )
            return []

        candidates = await self._extract_candidates(result)

        if not candidates:
            logger.debug(
                "LLM returned no candidates — trying regex fallback",
                extra={
                    "event": "analyst.llm_no_candidates",
                    "tool": result.tool,
                    "task_id": result.task_id,
                },
            )
            candidates = _regex_fallback(result)

        findings: list[Finding] = []
        dismissed = 0
        for candidate in candidates:
            confidence = float(candidate.get("confidence", 0.5))
            if confidence < _AUTO_DISMISS_BELOW:
                dismissed += 1
                logger.debug(
                    "Finding auto-dismissed (low confidence)",
                    extra={
                        "event": "analyst.finding_dismissed",
                        "reason": "confidence_below_threshold",
                        "confidence": round(confidence, 3),
                        "threshold": _AUTO_DISMISS_BELOW,
                        "tool": result.tool,
                        "task_id": result.task_id,
                    },
                )
                continue

            finding = self._build_finding(candidate, result)
            self._correlate(finding)
            findings.append(finding)

        logger.info(
            "Analyst findings extracted",
            extra={
                "event": "analyst.findings_extracted",
                "tool": result.tool,
                "task_id": result.task_id,
                "count": len(findings),
                "dismissed": dismissed,
            },
        )
        return findings

    # ── LLM extraction ─────────────────────────────────────────────────────

    async def _extract_candidates(self, result: ExecutorResult) -> list[dict[str, object]]:
        """
        Use LLM to extract structured finding candidates from raw tool output.

        Returns an empty list on any LLM failure so the regex fallback can run.
        """
        raw_output = (result.stdout or result.stderr)[:_RAW_OUTPUT_CAP]
        if not raw_output.strip():
            return []

        prompt = _ANALYST_PROMPT.format(
            tool=result.tool,
            target=result.target,
            raw_output=raw_output,
        )

        try:
            parsed = await self._llm.chat_json(
                [
                    {"role": "system", "content": _ANALYST_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
            )
            candidates = parsed.get("findings", [])
            if not isinstance(candidates, list):
                logger.warning(
                    "LLM findings field is not a list",
                    extra={
                        "event": "analyst.llm_bad_shape",
                        "tool": result.tool,
                        "task_id": result.task_id,
                    },
                )
                return []
            return candidates  # type: ignore[return-value]
        except LLMError as exc:
            logger.warning(
                "LLM extraction failed",
                extra={
                    "event": "analyst.llm_extraction_failed",
                    "tool": result.tool,
                    "task_id": result.task_id,
                    "error_type": type(exc).__name__,
                },
            )
            return []

    # ── Finding construction ───────────────────────────────────────────────

    def _build_finding(self, candidate: dict[str, object], result: ExecutorResult) -> Finding:
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
            cve_ids=[str(c) for c in candidate.get("cve_ids", [])],  # type: ignore[union-attr]
            tool_source=result.tool,
            raw_evidence=result.stdout[:2000],
            remediation=str(candidate.get("remediation", "")),
            discovered_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

    # ── Correlation ────────────────────────────────────────────────────────

    def _correlate(self, finding: Finding) -> None:
        """
        Check for relationships with existing validated findings.
        Heuristic: same target + overlapping CVE IDs → mark as related.
        """
        for existing in self._context.get_validated_findings():
            if existing.id == finding.id:
                continue
            if existing.target == finding.target and set(existing.cve_ids) & set(finding.cve_ids):
                finding.related_finding_ids.append(existing.id)
                logger.debug(
                    "Finding correlated with existing",
                    extra={
                        "event": "analyst.finding_correlated",
                        "finding_id": finding.id,
                        "correlated_with": existing.id,
                        "shared_cves": len(set(existing.cve_ids) & set(finding.cve_ids)),
                    },
                )


# ── Regex fallback ─────────────────────────────────────────────────────────────


def _regex_fallback(result: ExecutorResult) -> list[dict[str, object]]:
    """
    Extract findings from raw tool output using regex heuristics.

    Currently handles:
    - nmap: open port lines → one INFO/MEDIUM/HIGH finding per port
    """
    if result.tool == "nmap":
        return _parse_nmap_ports(result.stdout)
    return []


def _parse_nmap_ports(stdout: str) -> list[dict[str, object]]:
    """Parse nmap stdout for open port lines and build candidate dicts."""
    candidates: list[dict[str, object]] = []

    for match in _NMAP_OPEN_PORT_RE.finditer(stdout):
        port = match.group(1)
        proto = match.group(2)
        service = match.group(3).lower()
        version_info = (match.group(4) or "").strip()

        severity, confidence = _severity_for_service(service)

        title = f"Open {proto.upper()} port {port} ({service})"
        description = f"Port {port}/{proto} is open and running {service}."
        if version_info:
            description += f" Version info: {version_info}."

        candidates.append(
            {
                "title": title,
                "description": description,
                "severity": severity,
                "confidence": confidence,
                "port": port,
                "service": service,
                "cve_ids": [],
                "remediation": _remediation_hint(service),
            }
        )

    return candidates


def _severity_for_service(service: str) -> tuple[str, float]:
    """Return (severity, confidence) based on service name."""
    if service in _HIGH_RISK_SERVICES:
        return "high", 0.75
    if service in _MEDIUM_RISK_SERVICES:
        return "medium", 0.65
    return "info", 0.55


def _remediation_hint(service: str) -> str:
    hints: dict[str, str] = {
        "telnet": "Disable telnet; replace with SSH.",
        "ftp": "Disable FTP or switch to SFTP/FTPS.",
        "ssh": "Ensure key-based auth only; disable root login; update to latest version.",
        "http": "Review web application for vulnerabilities; ensure HTTPS is enforced.",
        "https": "Review TLS configuration and certificate validity.",
        "smb": "Restrict SMB access; disable SMBv1.",
        "rdp": "Restrict RDP to VPN; enable NLA; patch regularly.",
        "vnc": "Restrict VNC access; require strong authentication.",
        "snmp": "Use SNMPv3 with authentication; restrict community strings.",
    }
    return hints.get(service, "Review service necessity and access controls.")
