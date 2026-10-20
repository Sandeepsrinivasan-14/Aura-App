"""Per-repository settings and credential lookup.

Settings live in `.aura.json` at the repository root (safe to commit; it contains no secrets).
Tokens are never written to disk by Aura. They are read, in order, from:
  1. environment variables (GITHUB_TOKEN / GH_TOKEN, AZURE_DEVOPS_PAT / AURA_AZDO_PAT)
  2. the OS keychain via the optional `keyring` package (set with `aura login`)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal

CONFIG_FILE = ".aura.json"
KEYRING_SERVICE = "aura-git-assistant"

Mode = Literal["direct", "pull_request"]
AIBackend = Literal["rules", "ollama"]

TOKEN_ENV_VARS = {
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "azure_devops": ("AZURE_DEVOPS_PAT", "AURA_AZDO_PAT"),
}


@dataclass
class AuraConfig:
    mode: Mode = "pull_request"
    """direct: commit and push to the current branch. pull_request: commit to a work branch and open a PR."""
    base_branch: str = ""
    """Target branch for pull requests. Empty = the branch that was checked out when Aura started."""
    branch_prefix: str = "aura/"
    auto_merge: bool = False
    """Squash-merge the PR immediately after opening it. Off by default; enable only for solo repos."""
    push: bool = True
    debounce_seconds: float = 10.0
    """Wait this long after the last file change before proposing a commit."""
    cooldown_seconds: float = 60.0
    """Minimum time between two automatic commits."""
    ai_backend: AIBackend = "rules"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "mistral"
    ignore: list[str] = field(default_factory=list)
    """Extra glob patterns to ignore (in addition to .gitignore and .auraignore)."""

    @classmethod
    def load(cls, repo_path: str | Path) -> AuraConfig:
        path = Path(repo_path) / CONFIG_FILE
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown setting(s) in {path}: {', '.join(sorted(unknown))}")
        cfg = cls(**data)
        cfg.validate()
        return cfg

    def save(self, repo_path: str | Path) -> Path:
        self.validate()
        path = Path(repo_path) / CONFIG_FILE
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return path

    def validate(self) -> None:
        if self.mode not in ("direct", "pull_request"):
            raise ValueError(f"mode must be 'direct' or 'pull_request', not {self.mode!r}")
        if self.ai_backend not in ("rules", "ollama"):
            raise ValueError(f"ai_backend must be 'rules' or 'ollama', not {self.ai_backend!r}")
        if self.debounce_seconds < 0 or self.cooldown_seconds < 0:
            raise ValueError("debounce_seconds and cooldown_seconds must be >= 0")


def get_token(provider: str) -> str | None:
    for var in TOKEN_ENV_VARS.get(provider, ()):
        if os.environ.get(var):
            return os.environ[var]
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, provider)
    except Exception:  # no keychain backend available (e.g. headless Linux)
        return None


def set_token(provider: str, token: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("install 'keyring' (pip install aura-git[keyring]) or set an environment variable") from exc
    keyring.set_password(KEYRING_SERVICE, provider, token)
