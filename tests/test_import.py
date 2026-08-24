import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_package_imports():
    import research_dashboard

    assert research_dashboard.__name__ == "research_dashboard"


def test_checkout_root_imports_public_package_without_pythonpath():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import research_dashboard.cli; import research_dashboard.state",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
