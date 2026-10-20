"""Commit message generation.

`RuleBasedWriter` always works offline and produces Conventional Commit messages from the change set.
`OllamaWriter` asks a local LLM (via Ollama's HTTP API) for a summary line, validates it, and falls back to
the rule-based message whenever the model is unavailable, slow, or returns something unusable.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

import requests

from .git_ops import Change, DiffStat

log = logging.getLogger(__name__)

CONVENTIONAL = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w./-]+\))?!?: \S.{0,70}$")

DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
CONFIG_NAMES = {
    "requirements.txt",
    "pyproject.toml",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "dockerfile",
    "docker-compose.yml",
    "makefile",
}


@dataclass(frozen=True)
class ChangeSet:
    changes: list[Change]
    stats: list[DiffStat]
    diff: str = ""

    @property
    def paths(self) -> list[str]:
        return [c.path for c in self.changes]

    @property
    def lines_added(self) -> int:
        return sum(s.added for s in self.stats)

    @property
    def lines_deleted(self) -> int:
        return sum(s.deleted for s in self.stats)


def _category(path: str) -> str:
    p = PurePosixPath(path)
    name, parts = p.name.lower(), [x.lower() for x in p.parts]
    if parts and parts[0] in (".github", ".gitlab-ci.yml", ".circleci") or name in (".gitlab-ci.yml", "azure-pipelines.yml"):
        return "ci"
    in_test_dir = any(x in ("test", "tests", "__tests__", "spec") for x in parts[:-1])
    if in_test_dir or name.startswith("test_") or ".test." in name or ".spec." in name:
        return "test"
    if name in CONFIG_NAMES or name.endswith((".spec", ".lock")) or (name.startswith("requirements") and name.endswith(".txt")):
        return "build"
    if p.suffix.lower() in DOC_EXT or "docs" in parts[:-1] or name in ("license", "readme"):
        return "docs"
    return "code"


def _scope(paths: list[str]) -> str | None:
    tops = {PurePosixPath(p).parts[0] for p in paths if len(PurePosixPath(p).parts) > 1}
    if len(tops) == 1:
        scope = tops.pop()
        if scope not in ("src", "lib", "app") and re.fullmatch(r"[\w.-]+", scope):
            return scope
    return None


def _subject_target(changes: list[Change]) -> str:
    if len(changes) == 1:
        return PurePosixPath(changes[0].path).name
    return f"{len(changes)} files"


class RuleBasedWriter:
    name = "rules"

    def write(self, cs: ChangeSet) -> str:
        if not cs.changes:
            return "chore: no changes"
        cats = Counter(_category(c.path) for c in cs.changes)
        kinds = Counter(c.kind for c in cs.changes)
        scope = _scope(cs.paths)
        target = _subject_target(cs.changes)

        only_added = kinds["added"] == len(cs.changes)
        if len(cats) == 1 and "code" not in cats:
            ctype = next(iter(cats))  # docs / test / ci / build
        elif re.search(r"^\+(?!\+\+).*\b(fix(e[sd])?|bug|workaround|regression)\b", cs.diff, re.IGNORECASE | re.MULTILINE):
            ctype = "fix"
        elif only_added or (kinds["added"] and cats["code"]):
            ctype = "feat"
        elif cs.lines_added and cs.lines_deleted and abs(cs.lines_added - cs.lines_deleted) <= max(2, cs.lines_added // 10):
            ctype = "refactor"
        else:
            ctype = "chore"

        verb = {"added": "add", "deleted": "remove", "renamed": "rename"}.get(kinds.most_common(1)[0][0], "update")
        if ctype == "fix":
            verb = "fix"
        elif ctype == "refactor":
            verb = "refactor"
        header = f"{ctype}({scope}): " if scope else f"{ctype}: "
        subject = f"{verb} {target}"
        if len(header) + len(subject) > 72:
            subject = subject[: 72 - len(header) - 1] + "…"

        body_lines = [f"- {c.kind} {c.path}" for c in cs.changes[:20]]
        if len(cs.changes) > 20:
            body_lines.append(f"- ... and {len(cs.changes) - 20} more")
        stats = f"{len(cs.changes)} file(s), +{cs.lines_added}/-{cs.lines_deleted}"
        return f"{header}{subject}\n\n{stats}\n" + "\n".join(body_lines)


class OllamaWriter:
    name = "ollama"

    PROMPT = """Write a git commit message summary line for the diff below.
Rules: Conventional Commits format `type(optional-scope): description`; type is one of
feat, fix, docs, style, refactor, perf, test, build, ci, chore; imperative mood; lowercase; no trailing period;
at most 72 characters. Reply with the single line only.

Changed files:
{files}

Diff (may be truncated):
{diff}
"""

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "mistral",
        timeout: float = 60,
        fallback: RuleBasedWriter | None = None,
        session: requests.Session | None = None,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.fallback = fallback or RuleBasedWriter()
        self.session = session or requests.Session()
        self.last_source = "rules"

    def write(self, cs: ChangeSet) -> str:
        rule_message = self.fallback.write(cs)
        if not cs.changes:
            return rule_message
        prompt = self.PROMPT.format(files="\n".join(cs.paths[:50]), diff=cs.diff[:6000])
        try:
            resp = self.session.post(
                f"{self.url}/api/generate",
                timeout=self.timeout,
                json={"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2, "num_predict": 60}},
            )
            resp.raise_for_status()
            line = self.clean(resp.json().get("response", ""))
        except (requests.RequestException, ValueError) as exc:
            log.info("Ollama unavailable (%s); using rule-based message", exc)
            self.last_source = "rules"
            return rule_message
        if not line:
            self.last_source = "rules"
            return rule_message
        self.last_source = "ollama"
        body = rule_message.split("\n", 1)[1] if "\n" in rule_message else ""
        return f"{line}\n{body}" if body else line

    @staticmethod
    def clean(text: str) -> str | None:
        """Extract a valid Conventional Commit summary line from model output, or None."""
        for raw in text.strip().splitlines():
            line = raw.strip().strip("`\"'").rstrip(".").strip()
            line = re.sub(r"^(commit message|message|summary)\s*:\s*", "", line, flags=re.IGNORECASE)
            if not line:
                continue
            line = re.sub(r"^[A-Za-z]+", lambda m: m.group(0).lower(), line)  # "Feat:" -> "feat:"
            return line if CONVENTIONAL.match(line) else None
        return None


def make_writer(backend: str, url: str = "http://localhost:11434", model: str = "mistral"):
    return OllamaWriter(url, model) if backend == "ollama" else RuleBasedWriter()
