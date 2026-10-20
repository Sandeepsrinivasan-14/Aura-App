import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "AZURE_DEVOPS_PAT", "AURA_AZDO_PAT", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(var, raising=False)
    (home / ".gitconfig").write_text("[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n")


@pytest.fixture
def repo_dir(tmp_path) -> Path:
    """A repo with one commit on main and a local bare repository as 'origin'."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("# demo\n")
    (work / ".gitignore").write_text("build/\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "initial")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work


@pytest.fixture
def remote_dir(repo_dir) -> Path:
    return repo_dir.parent / "remote.git"
