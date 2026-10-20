"""Decide which file-system events should be ignored by the watcher.

Rules, in order: built-in noise patterns, `.auraignore`, extra patterns from `.aura.json`, and finally
whatever Git itself ignores (.gitignore, .git/info/exclude, global excludes) via `git check-ignore`.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path, PurePosixPath

DEFAULT_PATTERNS = [
    ".git/",
    "__pycache__/",
    ".venv/",
    "venv/",
    "node_modules/",
    ".idea/",
    ".vscode/",
    "*.pyc",
    "*.swp",
    "*.swx",
    "*~",
    "*.tmp",
    "*.log",
    ".DS_Store",
    "Thumbs.db",
    "4913",  # 4913: vim probe file
]


def load_auraignore(repo_path: str | Path) -> list[str]:
    path = Path(repo_path) / ".auraignore"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def _matches(rel: PurePosixPath, pattern: str) -> bool:
    if pattern.endswith("/"):
        name = pattern.rstrip("/")
        return name in rel.parts or fnmatch.fnmatch(str(rel), f"{name}/*")
    if "/" in pattern:
        return fnmatch.fnmatch(str(rel), pattern.lstrip("/"))
    return any(fnmatch.fnmatch(part, pattern) for part in rel.parts)


class IgnoreRules:
    def __init__(self, repo_path: str | Path, extra: list[str] | None = None, use_git: bool = True):
        self.root = Path(repo_path).resolve()
        self.patterns = DEFAULT_PATTERNS + load_auraignore(self.root) + list(extra or [])
        self.use_git = use_git

    def is_ignored(self, path: str | Path) -> bool:
        p = Path(path)
        try:
            rel = PurePosixPath(p.resolve().relative_to(self.root).as_posix())
        except ValueError:
            return True  # outside the repository
        if str(rel) in ("", "."):
            return True
        if any(_matches(rel, pat) for pat in self.patterns):
            return True
        return self.use_git and self._git_ignores(str(rel))

    def _git_ignores(self, rel: str) -> bool:
        try:
            result = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=self.root, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0
