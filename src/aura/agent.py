"""The commit workflow: detect changes -> write message -> (review) -> commit -> push -> pull request.

Pull-request mode never rewrites or switches away from the user's work: if they are on the base branch,
Aura creates a work branch (e.g. aura/2026-10-05) from the current state and keeps committing there; an
open PR for that branch is reused, so later commits simply update it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .commit_message import ChangeSet, RuleBasedWriter
from .config import AuraConfig, get_token
from .git_ops import GitError, GitRepo, basic_auth_header
from .providers import Provider, ProviderError, PullRequest, parse_remote, provider_for_remote

log = logging.getLogger(__name__)

# review(changeset, proposed_message) -> final message, or None to skip this commit
ReviewFn = Callable[[ChangeSet, str], "str | None"]


@dataclass
class RunResult:
    status: str  # "no_changes" | "skipped" | "committed" | "error"
    message: str = ""
    sha: str = ""
    branch: str = ""
    pushed: bool = False
    pull_request: PullRequest | None = None
    merged: bool = False
    notes: list[str] = field(default_factory=list)


def auto_approve(_cs: ChangeSet, message: str) -> str:
    return message


class Agent:
    def __init__(
        self,
        repo: GitRepo,
        config: AuraConfig,
        writer=None,
        provider: Provider | None = None,
        review: ReviewFn = auto_approve,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.repo = repo
        self.config = config
        self.writer = writer or RuleBasedWriter()
        self.review = review
        self.clock = clock
        self._provider = provider
        self._last_commit_at: float | None = None
        self.base_branch = config.base_branch or repo.current_branch()

    # ---------- helpers ----------

    def changeset(self) -> ChangeSet:
        return ChangeSet(self.repo.changes(), self.repo.diff_stats(), self.repo.diff())

    def provider(self) -> Provider | None:
        if self._provider is None:
            url = self.repo.remote_url()
            info = parse_remote(url) if url else None
            if info:
                token = get_token(info.kind)
                if token:
                    self._provider = provider_for_remote(url, token)
        return self._provider

    def in_cooldown(self) -> bool:
        return self._last_commit_at is not None and self.clock() - self._last_commit_at < self.config.cooldown_seconds

    def _work_branch(self) -> str:
        current = self.repo.current_branch()
        if self.config.mode != "pull_request" or current != self.base_branch:
            return current
        name = f"{self.config.branch_prefix}{datetime.now():%Y-%m-%d}"
        candidate, n = name, 2
        while self.repo.branch_exists(candidate):
            candidate, n = f"{name}-{n}", n + 1
        self.repo.create_branch(candidate)
        return candidate

    # ---------- main entry ----------

    def run_once(self, respect_cooldown: bool = False) -> RunResult:
        try:
            return self._run_once(respect_cooldown)
        except (GitError, ProviderError) as exc:
            log.error("%s", exc)
            return RunResult("error", notes=[str(exc)])

    def _run_once(self, respect_cooldown: bool) -> RunResult:
        if respect_cooldown and self.in_cooldown():
            return RunResult("skipped", notes=["cooldown active"])
        cs = self.changeset()
        if not cs.changes:
            return RunResult("no_changes")

        proposed = self.writer.write(cs)
        message = self.review(cs, proposed)
        if not message or not message.strip():
            return RunResult("skipped", message=proposed, notes=["rejected in review"])

        branch = self._work_branch()
        sha = self.repo.commit_all(message)
        self._last_commit_at = self.clock()
        result = RunResult("committed", message=message, sha=sha, branch=branch)
        log.info("committed %s on %s: %s", sha[:8], branch, message.splitlines()[0])

        if not self.config.push:
            return result
        if self.repo.remote_url() is None:
            result.notes.append("no 'origin' remote; commit kept locally")
            return result

        provider = self.provider()
        header = basic_auth_header(provider.git_username, provider.token) if provider else None
        self.repo.push(branch, auth_header=header)  # without a token, git's own credential helper is used
        result.pushed = True

        if self.config.mode == "pull_request" and branch != self.base_branch:
            if provider is None:
                result.notes.append("pushed, but no API token is configured, so no pull request was opened")
                return result
            title = message.splitlines()[0]
            body = message.split("\n", 1)[1].strip() if "\n" in message else ""
            pr = provider.open_or_reuse_pull_request(title, f"{body}\n\n_Opened by Aura._".strip(), branch, self.base_branch)
            result.pull_request = pr
            if self.config.auto_merge and not pr.existing:
                provider.merge_pull_request(pr)
                result.merged = True
        return result
