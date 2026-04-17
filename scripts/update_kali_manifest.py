#!/usr/bin/env python3
"""Fetch kali-meta debian/control and regenerate nemesis/tools/kali_tools.yml.

Dependency alternatives (a | b): first alternative is kept (Debian install order).
Does not modify markdown docs — see project plan.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import yaml

DEFAULT_CONTROL_URL = (
    "https://gitlab.com/kalilinux/packages/kali-meta/-/raw/kali/master/debian/control"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "nemesis/tools/kali_tools.yml"

# Menu / metapackage -> NEMESIS phase (for tools listed only in that menu).
MENU_TO_PHASE: dict[str, str] = {
    "kali-linux-headless": "scanning",
    "kali-tools-information-gathering": "recon",
    "kali-tools-vulnerability": "vulnerability",
    "kali-tools-web": "enumeration",
    "kali-tools-database": "enumeration",
    "kali-tools-passwords": "exploitation",
    "kali-tools-wireless": "exploitation",
    "kali-tools-reverse-engineering": "exploitation",
    "kali-tools-exploitation": "exploitation",
    "kali-tools-social-engineering": "exploitation",
    "kali-tools-sniffing-spoofing": "enumeration",
    "kali-tools-post-exploitation": "exploitation",
    "kali-tools-forensics": "forensics",
    "kali-tools-reporting": "recon",
    "kali-tools-identify": "recon",
    "kali-tools-protect": "vulnerability",
    "kali-tools-detect": "vulnerability",
    "kali-tools-respond": "exploitation",
    "kali-tools-recover": "forensics",
    "kali-tools-802-11": "exploitation",
    "kali-tools-bluetooth": "exploitation",
    "kali-tools-crypto-stego": "forensics",
    "kali-tools-fuzzing": "enumeration",
    "kali-tools-gpu": "exploitation",
    "kali-tools-hardware": "enumeration",
    "kali-tools-rfid": "enumeration",
    "kali-tools-sdr": "enumeration",
    "kali-tools-voip": "enumeration",
    "kali-tools-windows-resources": "exploitation",
    "kali-tools-top10": "enumeration",
}

PHASE_RANK: dict[str, int] = {
    "recon": 1,
    "forensics": 2,
    "scanning": 3,
    "enumeration": 4,
    "vulnerability": 5,
    "exploitation": 6,
}

# Debian source package -> argv binary name (when differs from package name).
PACKAGE_TO_BINARY: dict[str, str] = {
    "bind9-dnsutils": "dig",
    "iputils-arping": "arping",
    "netcat-traditional": "nc",
    "netcat-openbsd": "ncat",
    "nmap": "nmap",
    "samba-common-bin": "smbclient",
    "impacket-scripts": "impacket-smbclient",
    "python3-impacket": "impacket-smbclient",
    "dnsutils": "dig",
    "exploitdb": "searchsploit",
    "perl-cisco-copyconfig": "copy-router-config",
    "testssl.sh": "testssl.sh",
    "httpx-toolkit": "httpx",
    "openssl-provider-legacy": "openssl",
    "7zip": "7z",
    "vim": "vim",
    "vim-nox": "vim",
    "vim-tiny": "vim",
    "unrar": "unrar",
    "unar": "unar",
    "plocate": "plocate",
    "mlocate": "locate",
    "ettercap-graphical": "ettercap",
    "ettercap-text-only": "ettercap",
    "code-oss": "code",
    "powershell": "pwsh",
    "openssh-client-gssapi": "ssh",
    "openssh-client-ssh1": "ssh",
    "openssh-server": "sshd",
    "openssh-client": "ssh",
    "apache2": "apache2ctl",
    "default-mysql-server": "mysql",
    "ruby-pedump": "pedump",
    "crackmapexec": "netexec",
    "snmpcheck": "snmp-check",
    "theharvester": "theHarvester",
    "Responder": "Responder",
    "spiderfoot": "spiderfoot",
    "legion": "legion",
    "burpsuite": "burpsuite",
    "wireshark": "tshark",
    "metasploit-framework": "msfconsole",
    "powershell-empire": "empire",
    "beef-xss": "beef-xss",
    "set": "setoolkit",
}

PACKAGE_INVOCATION_PROFILE: dict[str, str] = {
    "nmap": "nmap_default_kali",
    "ffuf": "ffuf_kali",
    "gobuster": "gobuster_dir_kali",
    "nuclei": "nuclei_default_kali",
}

# default_args lists (package name keys; applied before binary rename for lookup by pkg)
PACKAGE_DEFAULT_ARGS_BY_PKG: dict[str, list[str]] = {
    "nikto": ["-h", "{target_url}", "-Format", "txt"],
    "whois": ["{target}"],
    "bind9-dnsutils": ["ANY", "{target}", "+noall", "+answer"],
    "dnsutils": ["ANY", "{target}", "+noall", "+answer"],
    "amass": ["enum", "-passive", "-d", "{target}"],
    "exploitdb": ["--json", "--disable-colour", "{target}"],
    "searchsploit": ["--json", "--disable-colour", "{target}"],
}

PACKAGE_OUTPUT_FORMAT: dict[str, str] = {
    "ffuf": "json",
    "exploitdb": "json",
    "searchsploit": "json",
}

# Ensure these Debian packages appear even if absent from parsed metas (nuclei is
# not in kali-linux-headless on current kali-meta; profile still ships in executor).
PROFILE_ENSURE_DEPS: list[tuple[str, list[str]]] = [
    ("kali-linux-headless", ["nuclei"]),
]

DESTRUCTIVE_PACKAGES: frozenset[str] = frozenset(
    {
        "hydra",
        "sqlmap",
        "msfconsole",
        "msfvenom",
        "metasploit-framework",
        "commix",
        "padbuster",
        "medusa",
        "ncrack",
        "patator",
        "hashcat",
        "john",
        "reaver",
        "bully",
        "mdk4",
        "wifite",
        "responder",
        "bettercap",
        "mitmproxy",
        "sslsplit",
        "dnschef",
        "ettercap-graphical",
        "ettercap-text-only",
        "beef-xss",
        "set",
        "powershell-empire",
        "evil-winrm",
        "crackmapexec",
        "netexec",
        "aircrack-ng",
        "hping3",
        "thc-ssl-dos",
        "davtest",
        "xsser",
        "yersinia",
        "sucrack",
        "thc-pptp-bruter",
        "goldeneye",
        "slowhttptest",
        "siege",
        "dhcpig",
        "t50",
        "inviteflood",
        "iaxflood",
        "rtpflood",
        "ntpsec-ntpdate",
    }
)

ROOT_PACKAGES: frozenset[str] = frozenset(
    {
        "arp-scan",
        "netdiscover",
        "tcpdump",
        "wireshark",
        "tshark",
        "netsniff-ng",
        "aircrack-ng",
        "kismet",
        "bettercap",
        "responder",
        "macchanger",
        "mitmproxy",
        "ettercap-graphical",
        "ettercap-text-only",
        "dsniff",
        "openvpn",
        "lynis",
    }
)

_ARCH_RE = re.compile(r"\s*\[[^\]]+\]\s*")
_VER_RE = re.compile(r"\s*\([^)]*\)\s*")


def split_stanzas(control_text: str) -> list[str]:
    """Split debian/control into raw stanza blocks (blank-line separated)."""
    lines = control_text.splitlines()
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append(cur)
    return ["\n".join(b) for b in blocks]


def parse_stanza_fields(stanza: str) -> dict[str, str]:
    """Parse one stanza into field name -> full value (newlines preserved in value)."""
    fields: dict[str, str] = {}
    current_key: str | None = None
    buf: list[str] = []

    for line in stanza.splitlines():
        if re.match(r"^[A-Za-z][A-Za-z0-9-]*\s*:", line):
            if current_key is not None:
                fields[current_key] = "\n".join(buf).strip()
            key, _, rest = line.partition(":")
            current_key = key.strip().lower()
            buf = [rest.strip()]
        elif line.startswith((" ", "\t")) and current_key is not None:
            buf.append(line)
        elif current_key is not None:
            buf.append(line.strip())

    if current_key is not None:
        fields[current_key] = "\n".join(buf).strip()
    return fields


def _strip_inline_comment(part: str) -> str:
    if "#" in part:
        return part[: part.index("#")].strip()
    return part.strip()


def normalize_depends_token(raw: str) -> str | None:
    """
    Turn one Depends fragment into a Debian package name or None if skipped.

    Alternatives (a | b): keep first after normalization.
    """
    part = _strip_inline_comment(raw)
    if not part or part.startswith("${"):
        return None
    # First alternative
    alt = part.split("|", 1)[0].strip()
    alt = _ARCH_RE.sub("", alt)
    alt = _VER_RE.sub("", alt).strip()
    alt = _strip_inline_comment(alt)
    if not alt or alt.startswith("${"):
        return None
    return alt


def iter_depends_package_names(depends_value: str) -> Iterator[str]:
    """Yield package names from a Depends or Recommends field body."""
    if not depends_value:
        return
    depth = 0
    start = 0
    for i, ch in enumerate(depends_value):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            token = depends_value[start:i]
            name = normalize_depends_token(token)
            if name:
                yield name
            start = i + 1
    token = depends_value[start:]
    name = normalize_depends_token(token)
    if name:
        yield name


def is_transitional_stanza(fields: dict[str, str]) -> bool:
    desc = fields.get("description", "").strip()
    first = desc.split("\n", 1)[0].strip().lower()
    return first.startswith("transitional package")


def collect_source_packages(
    stanzas: list[dict[str, str]],
    *,
    include_large: bool,
    include_everything: bool,
) -> list[tuple[str, list[str]]]:
    """
    Return list of (source_package_name, depends_package_names) for metapackages
    we pull tool lists from.
    """
    wanted_prefix = "kali-tools-"
    out: list[tuple[str, list[str]]] = []

    for fields in stanzas:
        pkg = fields.get("package", "").strip()
        if not pkg:
            continue
        if is_transitional_stanza(fields):
            continue

        use = (
            pkg == "kali-linux-headless"
            or pkg.startswith(wanted_prefix)
            or (include_large and pkg == "kali-linux-large")
            or (include_everything and pkg == "kali-linux-everything")
        )
        if not use:
            continue

        dep_lines: list[str] = []
        if fields.get("depends"):
            dep_lines.append(fields["depends"])
        raw = "\n".join(dep_lines)
        names = list(iter_depends_package_names(raw.replace("\n", " ")))
        out.append((pkg, names))
    return out


def resolve_binary(debian_pkg: str) -> str:
    return PACKAGE_TO_BINARY.get(debian_pkg, debian_pkg)


def should_skip_package(name: str) -> bool:
    return (
        name.startswith("kali-")
        or name.startswith("lib")
        or name.startswith("firmware-")
        or name.startswith("python3-")
        or name.startswith("ruby-")
        or name.startswith("php")
        or name.startswith("golang-")
        or name.startswith("linux-image")
        or name.startswith("linux-headers")
    )


def pick_phase(sources: set[str]) -> str:
    best = "recon"
    best_rank = 0
    for src in sources:
        ph = MENU_TO_PHASE.get(src, "recon")
        r = PHASE_RANK.get(ph, 0)
        if r > best_rank:
            best_rank = r
            best = ph
    return best


def build_tool_rows(
    source_entries: list[tuple[str, list[str]]],
) -> list[dict[str, object]]:
    """Merge by resolved binary; attach tags from source metapackages."""
    by_binary: dict[str, dict[str, object]] = {}

    for meta, deps in source_entries:
        for deb_pkg in deps:
            if should_skip_package(deb_pkg):
                continue
            binary = resolve_binary(deb_pkg)
            row = by_binary.setdefault(
                binary,
                {
                    "name": binary,
                    "binary": binary,
                    "sources": set(),
                    "debian_packages": set(),
                },
            )
            assert isinstance(row["sources"], set)
            assert isinstance(row["debian_packages"], set)
            row["sources"].add(meta)
            row["debian_packages"].add(deb_pkg)

    rows: list[dict[str, object]] = []
    for binary in sorted(by_binary):
        data = by_binary[binary]
        sources: set[str] = data["sources"]  # type: ignore[assignment]
        deb_pkgs: set[str] = data["debian_packages"]  # type: ignore[assignment]
        primary_pkg = sorted(deb_pkgs)[0]
        if primary_pkg == binary and len(deb_pkgs) > 1:
            primary_pkg = sorted(deb_pkgs, key=lambda p: (p != binary, p))[0]

        phase = pick_phase(sources)
        tags = sorted(sources)

        install_pkg = primary_pkg
        row: dict[str, object] = {
            "name": binary,
            "binary": binary,
            "phase": phase,
            "destructive": any(
                p in DESTRUCTIVE_PACKAGES or resolve_binary(p) in DESTRUCTIVE_PACKAGES
                for p in deb_pkgs
            ),
            "requires_root": any(p in ROOT_PACKAGES for p in deb_pkgs) or binary in ROOT_PACKAGES,
            "tags": tags,
            "install_hint": f"sudo apt install {install_pkg}",
            "description": f"Kali tool (Debian package: {install_pkg}). Source: kali-meta.",
        }

        for pkg in sorted(deb_pkgs):
            if pkg in PACKAGE_INVOCATION_PROFILE:
                row["invocation_profile"] = PACKAGE_INVOCATION_PROFILE[pkg]
                break
        if "invocation_profile" not in row:
            prof = PACKAGE_INVOCATION_PROFILE.get(binary)
            if prof:
                row["invocation_profile"] = prof

        for pkg in sorted(deb_pkgs):
            if pkg in PACKAGE_DEFAULT_ARGS_BY_PKG:
                row["default_args"] = PACKAGE_DEFAULT_ARGS_BY_PKG[pkg]
                break
        if "default_args" not in row and binary in PACKAGE_DEFAULT_ARGS_BY_PKG:
            row["default_args"] = PACKAGE_DEFAULT_ARGS_BY_PKG[binary]
        if "default_args" not in row and "invocation_profile" not in row:
            row["default_args"] = ["{target}"]

        out_fmt = "text"
        for pkg in sorted(deb_pkgs):
            if pkg in PACKAGE_OUTPUT_FORMAT:
                out_fmt = PACKAGE_OUTPUT_FORMAT[pkg]
                break
        if out_fmt == "text" and binary in PACKAGE_OUTPUT_FORMAT:
            out_fmt = PACKAGE_OUTPUT_FORMAT[binary]
        row["output_format"] = out_fmt

        rows.append(row)
    return rows


def fetch_control(url: str, timeout: float = 60.0) -> str:
    headers = {"User-Agent": "nemesis-kali-manifest-sync/1.0"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        r.raise_for_status()
        return r.text


def dump_yaml(tools: list[dict[str, object]], url: str) -> str:
    when = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"# NEMESIS Kali tool manifest — generated by scripts/update_kali_manifest.py\n"
        f"# Generated: {when}\n"
        f"# Source: {url}\n"
        f"#\n"
        f"# Placeholders in default_args:\n"
        f"#   {{target}}, {{target_url}}, {{wordlist_ffuf}}, {{wordlist_gobuster}}\n"
    )
    body = yaml.safe_dump(
        {"tools": tools},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return header + "\n" + body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_CONTROL_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-large", action="store_true")
    parser.add_argument("--include-everything", action="store_true")
    args = parser.parse_args()

    text = fetch_control(args.url)
    stanzas = [parse_stanza_fields(s) for s in split_stanzas(text)]
    sources = collect_source_packages(
        stanzas,
        include_large=args.include_large,
        include_everything=args.include_everything,
    )
    rows = build_tool_rows(sources + PROFILE_ENSURE_DEPS)
    out = dump_yaml(rows, args.url)

    if args.dry_run:
        print(f"tools: {len(rows)}")
        for r in rows[:30]:
            print(f"  - {r['name']}")
        if len(rows) > 30:
            print(f"  ... ({len(rows) - 30} more)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
