"""An explicit isolated-session config must be selected before any action."""
import io
import sys
import types

from virtuoso_design_agent.adapters import bridge_worker


def test_worker_selects_explicit_config_before_dispatch(monkeypatch, tmp_path):
    config = tmp_path / "isolated.env"
    config.touch()
    events = []
    module = types.ModuleType("virtuoso_bridge.env")
    module.set_runtime_env_file = lambda path: events.append(("config", path))
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.env", module)
    monkeypatch.setenv("VDA_BRIDGE_ENV_FILE", str(config))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"action":"config_test"}'))
    monkeypatch.setitem(bridge_worker._ACTIONS, "config_test", lambda _: events.append(("action", None)))
    assert bridge_worker.main() == 0
    assert events == [("config", config), ("action", None)]


def test_missing_explicit_config_never_dispatches(monkeypatch, tmp_path, capsys):
    module = types.ModuleType("virtuoso_bridge.env")
    module.set_runtime_env_file = lambda _: None
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.env", module)
    monkeypatch.setenv("VDA_BRIDGE_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"action":"config_test"}'))
    called = []
    monkeypatch.setitem(bridge_worker._ACTIONS, "config_test", lambda _: called.append(True))
    assert bridge_worker.main() == 1
    assert not called
    assert "must name an existing absolute file" in capsys.readouterr().out
