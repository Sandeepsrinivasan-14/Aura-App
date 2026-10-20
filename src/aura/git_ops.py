"""Thin, safe wrapper around the git command line.

Design notes
- Uses the `git` executable so hooks, credential helpers, .gitignore and LFS behave exactly as the user expects.
- Previews (diff, stats) are computed in a temporary index, so looking at changes never alters what the user staged.
- Push credentials are passed as an HTTP header through GIT_CONFIG_* environment variables: the token is never
  written into the remote URL, .git/config or the process argument list.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    status: str  # two-letter porcelain status, e.g. " M", "??", "A ", " D", "R "
    path: str

    @property
    def kind(self) -> str:
        s = self.status
        if s == "??" or "A" in s:
            return "added"
        if "D" in s:
            return "deleted"
        if "R" in s:
            return "renamed"
        return "modified"


@dataclass(frozen=True)
class DiffStat:
    path: str
    added: int
    deleted: int
    binary: bool = False


def basic_auth_header(username: str, token: str) -> str:
    raw = base64.b64encode(f"{username}:{token}".encode()).decode()
    return f"Authorization: Basic {raw}"


class GitRepo:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_dir():
            raise GitError(f"{self.path} is not a directory")
        try:
            top = self._run("rev-parse", "--show-toplevel").strip()
        except GitError as exc:
            raise GitError(f"{self.path} is not inside a Git repository") from exc
        self.path = Path(top).resolve()

    # ---------- plumbing ----------

    def _run(self, *args: str, env: dict | None = None, secret: str | None = None, check: bool = True) -> str:
        full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})}
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.path,
                capture_output=True,
                text=True,
                env=full_env,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except FileNotFoundError as exc:
            raise GitError("git is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out") from exc
        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip()
            if secret:
                message = message.replace(secret, "***")
            raise GitError(f"git {args[0]} failed: {message}")
        return proc.stdout

    def _with_temp_index(self, *args: str) -> str:
        """Run a git command against a throw-away index containing *all* working-tree changes."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if self.has_commits():
                self._run("read-tree", "HEAD", env=env)
            self._run("add", "-A", env=env)
            return self._run(*args, env=env)

    # ---------- queries ----------

    def has_commits(self) -> bool:
        return self._run("rev-parse", "--verify", "-q", "HEAD", check=False).strip() != ""

    def current_branch(self) -> str:
        name = self._run("symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
        if not name:
            raise GitError("HEAD is detached; check out a branch first")
        return name

    def branch_exists(self, name: str) -> bool:
        return self._run("rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False).strip() != ""

    def remote_url(self, remote: str = "origin") -> str | None:
        url = self._run("remote", "get-url", remote, check=False).strip()
        return url or None

    def changes(self) -> list[Change]:
        out = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries, items, i = [], out.split("\0"), 0
        while i < len(items):
            entry = items[i]
            if len(entry) < 4:
                i += 1
                continue
            status, path = entry[:2], entry[3:]
            entries.append(Change(status, path))
            i += 2 if status[0] in "RC" else 1  # renames carry the old path as an extra field
        return entries

    def is_dirty(self) -> bool:
        return bool(self.changes())

    def diff(self, max_chars: int = 200_000) -> str:
        """Unified diff of every working-tree change (tracked and untracked) against HEAD."""
        text = self._with_temp_index("diff", "--cached", "--no-color")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... diff truncated ({len(text) - max_chars} more characters)\n"
        return text

    def diff_stats(self) -> list[DiffStat]:
        out = self._with_temp_index("diff", "--cached", "--numstat", "-z", "--no-renames")
        stats = []
        for record in filter(None, out.split("\0")):
            added, deleted, path = record.split("\t", 2)
            binary = added == "-"
            stats.append(DiffStat(path, 0 if binary else int(added), 0 if binary else int(deleted), binary))
        return stats

    # ---------- mutations ----------

    def create_branch(self, name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", name) or ".." in name or name.endswith((".", "/", ".lock")):
            raise GitError(f"invalid branch name {name!r}")
        self._run("switch", "-c", name)

    def switch(self, name: str) -> None:
        self._run("switch", name)

    def commit_all(self, message: str) -> str:
        """Stage everything (respecting .gitignore), commit with `message` and return the new commit SHA."""
        if not message.strip():
            raise GitError("commit message is empty")
        self._run("add", "-A")
        fd, msg_file = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(message if message.endswith("\n") else message + "\n")
            self._run("commit", "-q", "-F", msg_file)
        finally:
            os.unlink(msg_file)
        return self._run("rev-parse", "HEAD").strip()

    def push(self, branch: str, remote: str = "origin", auth_header: str | None = None) -> None:
        env = None
        secret = None
        if auth_header:
            env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": auth_header}
            secret = auth_header.split()[-1]
        self._run("push", "-u", remote, f"{branch}:{branch}", env=env, secret=secret)
