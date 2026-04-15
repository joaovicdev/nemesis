"""Analyst agent — processes raw executor output into structured, scored findings.

The Analyst is the gatekeeper between raw tool output and the Orchestrator.
Pipeline per executor result:
  1. Extract candidate findings via LLM (JSON) — falls back to regex if LLM fails.
  2. Score confidence (0.0 – 1.0) and auto-dismiss noise below threshold.
  3. Correlate new findings against existing validated ones in ProjectContext.
  4. Return findings with status=UNVERIFIED, ready for Orchestrator promotion.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime

from nemesis.agents.executor import ExecutorResult, ToolNotFoundError, get_executor
from nemesis.agents.llm_client import LLMClient, LLMError
from nemesis.core.project import ProjectContext
from nemesis.db.models import Finding, FindingSeverity, FindingStatus

logger = logging.getLogger(__name__)

_AUTO_DISMISS_BELOW = 0.25
_NEEDS_REVIEW_BELOW = 0.60

_RAW_OUTPUT_CAP = 4000
_SEARCHSPLOIT_CAP = 5  # max exploits per CVE to avoid bloating the finding

_ANALYST_SYSTEM = (
    "You are a penetration testing analyst in an authorized assessment platform. "
    "Extract security-relevant findings from raw tool output and write them in a defensive, "
    "report-ready tone. "
    "Do not include credentials, exploit payloads, or instructions aimed at harming third parties. "
    "Focus on threat modeling: attacker prerequisites, abuse path at a high level, impact, and "
    "practical remediation guidance. "
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
  - attack_path_steps: list of ordered steps explaining how an attacker could abuse this finding
    in an authorized test (high-level, no payloads/credentials)
  - impact_assessment: short impact analysis (confidentiality/integrity/availability + practical abuse)
  - severity: "critical" | "high" | "medium" | "low" | "info"
  - confidence: float 0.0–1.0 (how certain you are this is a real finding)
  - port: port number as string, or "" if not applicable
  - service: service name (e.g. "ssh", "http"), or ""
  - cve_ids: list of CVE IDs if known, e.g. ["CVE-2021-1234"], or []
  - remediation: brief executive remediation summary (1–3 sentences), or ""
  - remediation_guidance: detailed remediation checklist and verification steps, or ""

Reply with this exact JSON structure:
{{
  "findings": [
    {{
      "title": "...",
      "description": "...",
      "attack_path_steps": ["...", "..."],
      "impact_assessment": "...",
      "severity": "info",
      "confidence": 0.8,
      "port": "22",
      "service": "ssh",
      "cve_ids": [],
      "remediation": "..."
      "remediation_guidance": "..."
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

# amass output format: one subdomain per line, e.g. "sub.example.com"
_AMASS_SUBDOMAIN_RE = re.compile(r"^([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})$", re.MULTILINE)

# Nuclei output format: [severity] [template-id] [matched-url]
_NUCLEI_LINE_RE = re.compile(
    r"\[(?P<severity>critical|high|medium|low|info)\]\s+"
    r"\[(?P<template>[^\]]+)\]\s+"
    r"(?P<url>\S+)",
    re.IGNORECASE,
)


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

        await self._enrich_with_exploits(findings)

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

    async def _enrich_with_exploits(self, findings: list[Finding]) -> None:
        """
        For each finding that has CVE IDs, query searchsploit locally and append any
        found exploit references to the finding's description and remediation.

        Mutates findings in-place. If searchsploit is not installed, returns without
        error (ToolNotFoundError from run() is also ignored per CVE).
        """
        if not shutil.which("searchsploit"):
            logger.debug(
                "searchsploit not found on PATH — skipping exploit enrichment",
                extra={"event": "analyst.searchsploit_not_found"},
            )
            return

        for finding in findings:
            if not finding.cve_ids:
                continue

            exploit_refs: list[str] = []
            for cve in finding.cve_ids[:3]:
                try:
                    executor = get_executor("searchsploit", "enrich", cve)
                    exec_result = await executor.run()
                except (ToolNotFoundError, ValueError):
                    continue

                if not exec_result.stdout.strip():
                    continue

                try:
                    data = json.loads(exec_result.stdout)
                except json.JSONDecodeError:
                    continue

                exploits = data.get("RESULTS_EXPLOIT", [])
                if not isinstance(exploits, list):
                    continue
                for exp in exploits[:_SEARCHSPLOIT_CAP]:
                    if not isinstance(exp, dict):
                        continue
                    title = exp.get("Title", "")
                    edb_id = exp.get("EDB-ID", "")
                    exp_type = exp.get("Type", "")
                    if title and edb_id:
                        exploit_refs.append(f"EDB-{edb_id} [{exp_type}]: {title}")

            if exploit_refs:
                refs_text = "\n".join(f"  • {r}" for r in exploit_refs)
                finding.description += f"\n\nKnown exploits:\n{refs_text}"
                finding.remediation = (
                    f"Patch immediately — public exploits exist. {finding.remediation}"
                    if finding.remediation
                    else "Patch immediately — public exploits exist."
                )
                finding.updated_at = datetime.utcnow()
                logger.info(
                    "Finding enriched with exploit references",
                    extra={
                        "event": "analyst.exploit_refs_added",
                        "exploit_count": len(exploit_refs),
                    },
                )

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

        attack_steps_raw = candidate.get("attack_path_steps", [])
        attack_steps: list[str] = []
        if isinstance(attack_steps_raw, list):
            attack_steps = [str(s) for s in attack_steps_raw if str(s).strip()]

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
            attack_path_steps=attack_steps,
            impact_assessment=str(candidate.get("impact_assessment", "")),
            remediation_guidance=str(candidate.get("remediation_guidance", "")),
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
    - amass: subdomain lines → one INFO finding per discovered subdomain
    - nuclei: silent CLI lines → one finding per template match
    - ffuf: JSON results array → one finding per matched URL
    """
    if result.tool == "nmap":
        return _parse_nmap_ports(result.stdout)
    if result.tool == "amass":
        return _parse_amass_output(result.stdout)
    if result.tool == "nuclei":
        return _parse_nuclei_output(result.stdout)
    if result.tool == "ffuf":
        return _parse_ffuf_output(result.stdout)
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


def _parse_amass_output(stdout: str) -> list[dict[str, object]]:
    """Parse amass stdout for discovered subdomains and build candidate dicts."""
    subdomains = _AMASS_SUBDOMAIN_RE.findall(stdout)
    return [
        {
            "title": f"Subdomain discovered: {sub}",
            "description": f"amass found subdomain {sub} via passive enumeration.",
            "severity": "info",
            "confidence": 0.85,
            "port": "",
            "service": "dns",
            "cve_ids": [],
            "remediation": "Review subdomain for exposed services.",
        }
        for sub in subdomains
    ]


def _parse_ffuf_output(stdout: str) -> list[dict[str, object]]:
    """Parse ffuf JSON output into finding candidates."""
    candidates: list[dict[str, object]] = []
    start = stdout.find("{")
    if start < 0:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return []

    results = data.get("results", [])
    if not isinstance(results, list):
        return []

    sensitive_keywords = {
        "admin",
        "login",
        "wp-admin",
        "phpmyadmin",
        "config",
        "backup",
        "api",
        ".git",
        ".env",
        "console",
        "manager",
        "dashboard",
    }

    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "") or "")
        if not url:
            continue
        status = item.get("status", 0)
        length = item.get("length", 0)
        words = item.get("words", 0)

        path = url.rsplit("/", 1)[-1].lower() if "/" in url else url.lower()
        is_sensitive = any(kw in path for kw in sensitive_keywords)
        severity = "medium" if is_sensitive else "info"
        confidence = 0.75 if is_sensitive else 0.55
        port = "443" if url.lower().startswith("https://") else "80"

        candidates.append(
            {
                "title": f"Web path discovered: {url}",
                "description": (
                    f"ffuf found accessible path '{url}' "
                    f"(HTTP {status}, {length} bytes, {words} words)."
                ),
                "severity": severity,
                "confidence": confidence,
                "port": port,
                "service": "http",
                "cve_ids": [],
                "remediation": (
                    f"Review '{url}' for sensitive data exposure or unauthorized access."
                    if is_sensitive
                    else f"Verify that '{url}' should be publicly accessible."
                ),
            }
        )

    return candidates


def _parse_nuclei_output(stdout: str) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    confidence_map = {
        "critical": 0.9,
        "high": 0.8,
        "medium": 0.7,
        "low": 0.55,
        "info": 0.5,
    }
    for match in _NUCLEI_LINE_RE.finditer(stdout):
        severity = match.group("severity").lower()
        template = match.group("template")
        url = match.group("url")
        confidence = confidence_map.get(severity, 0.6)
        cve_ids: list[str] = [template] if template.upper().startswith("CVE-") else []
        candidates.append(
            {
                "title": f"Nuclei: {template}",
                "description": f"nuclei template '{template}' matched on {url}.",
                "severity": severity,
                "confidence": confidence,
                "port": "",
                "service": "http",
                "cve_ids": cve_ids,
                "remediation": f"Review and remediate '{template}' finding on {url}.",
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
