"""Unit tests for scripts/update_kali_manifest.py control parsing helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts/update_kali_manifest.py"

_spec = importlib.util.spec_from_file_location("update_kali_manifest", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

split_stanzas = _mod.split_stanzas
parse_stanza_fields = _mod.parse_stanza_fields
iter_depends_package_names = _mod.iter_depends_package_names
normalize_depends_token = _mod.normalize_depends_token
collect_source_packages = _mod.collect_source_packages


def test_normalize_depends_token_alternatives_and_arch() -> None:
    assert normalize_depends_token("arping | iputils-arping") == "arping"
    assert normalize_depends_token("ffuf [amd64 arm64]") == "ffuf"
    assert normalize_depends_token("samba-common-bin (>= 2:4.21.2+dfsg-3)") == "samba-common-bin"


def test_normalize_depends_token_substvars_skipped() -> None:
    assert normalize_depends_token("${misc:Depends}") is None


def test_iter_depends_commas_misc() -> None:
    body = "${misc:Depends}, nmap, ffuf [amd64], hydra | medusa"
    names = list(iter_depends_package_names(body))
    assert names == ["nmap", "ffuf", "hydra"]


def test_split_and_parse_stanza_minimal() -> None:
    control = """
Package: kali-linux-headless
Architecture: any
Depends: ${misc:Depends},
 nmap,
 ffuf,
 arping | iputils-arping,
 samba-common-bin (>= 2:4.21.2+dfsg-3)
Description: test meta

Package: kali-tools-web
Architecture: any
Depends: nikto, sqlmap
Description: web menu
""".strip()
    stanzas = [parse_stanza_fields(s) for s in split_stanzas(control)]
    assert len(stanzas) == 2
    h = next(s for s in stanzas if s.get("package") == "kali-linux-headless")
    deps = list(iter_depends_package_names(h["depends"].replace("\n", " ")))
    assert deps == ["nmap", "ffuf", "arping", "samba-common-bin"]
    w = next(s for s in stanzas if s.get("package") == "kali-tools-web")
    assert list(iter_depends_package_names(w["depends"])) == ["nikto", "sqlmap"]


def test_collect_skips_transitional() -> None:
    control = """
Package: kali-desktop-i3-gaps
Section: oldlibs
Architecture: all
Depends: kali-desktop-i3
Description: transitional package
 This is a transitional package.

Package: kali-tools-web
Architecture: any
Depends: nikto
Description: web tools
""".strip()
    stanzas = [parse_stanza_fields(s) for s in split_stanzas(control)]
    src = collect_source_packages(stanzas, include_large=False, include_everything=False)
    assert len(src) == 1
    assert src[0][0] == "kali-tools-web"
