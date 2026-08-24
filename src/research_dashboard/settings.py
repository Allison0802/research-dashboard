from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_RUNTIME_ROOT = Path.home() / ".research-dashboard"


@dataclass(frozen=True)
class Settings:
    runtime_root: Path

    @property
    def database_path(self) -> Path:
        return self.runtime_root / "dashboard.sqlite3"


def load_settings() -> Settings:
    override = os.environ.get("RESEARCH_DASHBOARD_HOME")
    root = Path(override).expanduser() if override else Path.home() / ".research-dashboard"
    return Settings(runtime_root=root)
