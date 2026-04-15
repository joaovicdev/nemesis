"""Tests for ProjectContext scope validation."""

from __future__ import annotations

import pytest

from nemesis.core.project import ProjectContext, _target_matches
from nemesis.db.models import Project, Session


def _make_ctx(targets: list[str], out_of_scope: list[str] | None = None) -> ProjectContext:
    project = Project(name="test", targets=targets, out_of_scope=out_of_scope or [])
    session = Session(project_id=project.id)
    return ProjectContext(project=project, session=session)


@pytest.mark.parametrize(
    ("candidate", "scope", "expected"),
    [
        ("192.168.1.1", "192.168.1.1", True),
        ("example.com", "example.com", True),
        ("sub.example.com", "example.com", True),
        ("other.com", "example.com", False),
        ("192.168.1.10", "192.168.1.0/24", True),
        ("192.168.2.1", "192.168.1.0/24", False),
        ("10.0.0.1", "10.0.0.0/8", True),
        ("192.168.1.0/28", "192.168.1.0/24", True),
        ("192.168.2.0/24", "192.168.1.0/24", False),
        ("192.168.1.0/24", "192.168.1.0/24", True),
    ],
)
def test_target_matches(candidate: str, scope: str, expected: bool) -> None:
    assert _target_matches(candidate, scope) == expected


def test_ip_in_cidr_scope() -> None:
    ctx = _make_ctx(["192.168.1.0/24"])
    assert ctx.is_in_scope("192.168.1.50") is True
    assert ctx.is_in_scope("192.168.2.1") is False


def test_out_of_scope_takes_priority() -> None:
    ctx = _make_ctx(["192.168.1.0/24"], out_of_scope=["192.168.1.1"])
    assert ctx.is_in_scope("192.168.1.1") is False
    assert ctx.is_in_scope("192.168.1.2") is True


def test_mixed_targets() -> None:
    ctx = _make_ctx(["192.168.1.0/24", "target.corp.local"])
    assert ctx.is_in_scope("192.168.1.100") is True
    assert ctx.is_in_scope("sub.target.corp.local") is True
    assert ctx.is_in_scope("other.com") is False


def test_cidr_out_of_scope() -> None:
    ctx = _make_ctx(["10.0.0.0/8"], out_of_scope=["10.0.0.0/24"])
    assert ctx.is_in_scope("10.0.0.5") is False
    assert ctx.is_in_scope("10.1.0.5") is True
