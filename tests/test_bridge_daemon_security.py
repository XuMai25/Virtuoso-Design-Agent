from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

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


def _install_bridge_guard(
    monkeypatch,
    *,
    outcome: SimpleNamespace | None = None,
    error: Exception | None = None,
) -> list[tuple[object, int]]:
    package = ModuleType("virtuoso_bridge")
    package.__path__ = []
    guard = ModuleType("virtuoso_bridge.daemon_guard")
    calls: list[tuple[object, int]] = []

    def check_daemon_bind(client, *, timeout: int):
        calls.append((client, timeout))
        if error is not None:
            raise error
        return outcome

    guard.check_daemon_bind = check_daemon_bind
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", package)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.daemon_guard", guard)
    return calls


@pytest.mark.parametrize("bind", ["127.0.0.1:65346", "[::1]:65346"])
def test_bridge_daemon_guard_accepts_loopback(monkeypatch, bind: str) -> None:
    client = object()
    calls = _install_bridge_guard(
        monkeypatch,
        outcome=SimpleNamespace(ok=True, daemon_bind=bind, error=""),
    )

    actual = _require_loopback_bridge_daemon(client)

    assert actual == bind
    assert calls == [(client, 5)]


def test_bridge_daemon_guard_rejects_all_interfaces(monkeypatch) -> None:
    _install_bridge_guard(
        monkeypatch,
        outcome=SimpleNamespace(
            ok=False,
            daemon_bind="0.0.0.0:65346",
            error="daemon is listening on non-loopback address '0.0.0.0:65346'",
        ),
    )

    with pytest.raises(RuntimeError, match="non-loopback address"):
        _require_loopback_bridge_daemon(object())


def test_bridge_daemon_guard_rejects_missing_runtime_evidence(monkeypatch) -> None:
    _install_bridge_guard(
        monkeypatch,
        outcome=SimpleNamespace(
            ok=False,
            daemon_bind="",
            error="daemon did not report its actual bind address",
        ),
    )

    with pytest.raises(RuntimeError, match="did not report"):
        _require_loopback_bridge_daemon(object())


def test_bridge_daemon_guard_wraps_query_error(monkeypatch) -> None:
    _install_bridge_guard(monkeypatch, error=RuntimeError("Empty response from daemon"))

    with pytest.raises(RuntimeError, match="could not verify.*Empty response"):
        _require_loopback_bridge_daemon(object())


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
