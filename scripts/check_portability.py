"""Fail closed on portable-source violations in a candidate repository."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "build",
        "dist",
        "htmlcov",
    }
)

TEXT_PATTERNS = (
    (
        "absolute user-home path",
        re.compile(
            r"(?:/(?:Users|home)/[A-Za-z0-9_.-]+(?:/|$)|"
            r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+(?:[\\/]|$))",
            re.IGNORECASE,
        ),
    ),
    (
        "cloud-storage root",
        re.compile(
            r"(?:^|[\"'(\s])(?:~?[\\/]|(?:[A-Za-z0-9_.-]+[\\/])*Library[\\/]|"
            r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+[\\/])"
            r"(?:CloudStorage|OneDrive|Dropbox)(?:[\\/]|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "platform-specific application-data path",
        re.compile(
            r"(?:(?:^|[\"'(\s])(?:~?[\\/])?Library[\\/]Application Support|"
            r"(?:^|[\"'(\s])AppData[\\/](?:Roaming|Local|LocalLow)|"
            r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+[\\/]AppData[\\/]"
            r"(?:Roaming|Local|LocalLow))(?:[\\/]|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "local agent configuration path",
        re.compile(
            r"(?:^|[~\\/])\.(?:codex|claude|agents|cursor|ai[-_]bridge)(?:[\\/]|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "credential assignment",
        re.compile(
            r"\b(?:(?:api|access|auth|secret|private)[_-]?"
            r"(?:key|token|password|secret)|api[_-]?(?:key|token)|"
            r"(?:key|token|password|passwd))\b\s*[:=]\s*"
            r"[\"']?[A-Za-z0-9_-]{12,}",
            re.IGNORECASE,
        ),
    ),
    (
        "private-key material",
        re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)
RUNTIME_SUFFIXES = frozenset({".db", ".log", ".sqlite", ".sqlite3"})


@dataclass(frozen=True)
class PortabilityIssue:
    path: Path
    line: int | None
    message: str

    def format(self) -> str:
        location = str(self.path)
        if self.line is not None:
            location = f"{location}:{self.line}"
        return f"{location}: {self.message}"


def _is_excluded(path: Path) -> bool:
    return any(
        part in EXCLUDED_DIRECTORIES or part.endswith(".egg-info")
        for part in path.parts
    )


def _is_synthetic_test_fixture(path: Path) -> bool:
    return path.parts[:3] == ("tests", "fixtures", "synthetic")


def _iter_repository_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        relative_path = path.relative_to(root)
        if _is_excluded(relative_path) or not path.is_file() or path.is_symlink():
            continue
        yield path


def find_portability_issues(root: Path | str) -> list[PortabilityIssue]:
    """Return portable-source violations under ``root`` without changing files."""

    root_path = Path(root).resolve()
    issues: list[PortabilityIssue] = []
    for path in _iter_repository_files(root_path):
        relative_path = path.relative_to(root_path)
        if path.suffix.casefold() in RUNTIME_SUFFIXES and not _is_synthetic_test_fixture(
            relative_path
        ):
            issues.append(
                PortabilityIssue(
                    relative_path,
                    None,
                    "runtime database or log file is not an explicitly synthetic test fixture",
                )
            )
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for description, pattern in TEXT_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                issues.append(PortabilityIssue(relative_path, line, description))

    return sorted(issues, key=lambda issue: (str(issue.path), issue.line or 0, issue.message))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to scan (defaults to this repository)",
    )
    args = parser.parse_args()
    issues = find_portability_issues(args.root)
    if not issues:
        print("portability scan: PASS")
        return 0

    for issue in issues:
        print(issue.format())
    print(f"portability scan: FAIL ({len(issues)} finding(s))")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
