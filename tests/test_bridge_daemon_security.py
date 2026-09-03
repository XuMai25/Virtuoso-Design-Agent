from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.bridge_worker import (
    _require_loopback_bridge_daemon,
)


class _FakeClient:
    def __init__(self, *, output: str = "", errors: list[str] | None = None) -> None:
        self.output = output
        self.errors = errors or []
        self.calls: list[tuple[str, int]] = []

    def execute_skill(self, expression: str, timeout: int):
        self.calls.append((expression, timeout))
        return SimpleNamespace(output=self.output, errors=self.errors)


@pytest.mark.parametrize("bind", ['"127.0.0.1:65346"', '"[::1]:65346"'])
def test_bridge_daemon_guard_accepts_loopback(bind: str) -> None:
    client = _FakeClient(output=bind)

    actual = _require_loopback_bridge_daemon(client)

    assert actual == bind.strip('"')
    assert client.calls == [("if(boundp('RBLastBind) RBLastBind \"\")", 5)]


def test_bridge_daemon_guard_rejects_all_interfaces() -> None:
    client = _FakeClient(output='"0.0.0.0:65346"')

    with pytest.raises(RuntimeError, match="non-loopback address"):
        _require_loopback_bridge_daemon(client)


@pytest.mark.parametrize(
    ("output", "errors", "message"),
    [
        ('""', [], "did not report"),
        ("", ["Empty response from daemon"], "could not verify"),
    ],
)
def test_bridge_daemon_guard_fails_closed_without_evidence(
    output: str,
    errors: list[str],
    message: str,
) -> None:
    client = _FakeClient(output=output, errors=errors)

    with pytest.raises(RuntimeError, match=message):
        _require_loopback_bridge_daemon(client)


def test_probe_records_verified_daemon_bind_as_bridge_readback(monkeypatch) -> None:
    client = _FakeClient(output='"3"')
    client._vda_daemon_bind = "127.0.0.1:65346"
    monkeypatch.setattr(bridge_worker, "_client", lambda: client)
    monkeypatch.setitem(
        sys.modules,
        "virtuoso_bridge",
        SimpleNamespace(__version__="0.7.0"),
    )

    data = bridge_worker.probe({"profile": {"name": "nics4304_tsmc28"}})

    assert data["connected"] is True
    assert data["skill_probe"] == "3"
    assert data["daemon_bind"] == "127.0.0.1:65346"
