from pathlib import Path

from research_dashboard.settings import load_settings


def test_default_runtime_root_uses_home(monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCH_DASHBOARD_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert load_settings().runtime_root == tmp_path / ".research-dashboard"


def test_runtime_root_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "runtime"
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(custom))
    assert load_settings().runtime_root == custom
