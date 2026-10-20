import threading
import time

import pytest

from aura.config import AuraConfig, get_token
from aura.ignore import IgnoreRules
from aura.watcher import RepoWatcher


def test_config_roundtrip_and_validation(tmp_path):
    cfg = AuraConfig(mode="direct", auto_merge=True, ignore=["*.csv"])
    cfg.save(tmp_path)
    loaded = AuraConfig.load(tmp_path)
    assert loaded == cfg
    assert "token" not in (tmp_path / ".aura.json").read_text().lower()

    (tmp_path / ".aura.json").write_text('{"mode": "yolo"}')
    with pytest.raises(ValueError, match="mode"):
        AuraConfig.load(tmp_path)
    (tmp_path / ".aura.json").write_text('{"github_pat": "x"}')
    with pytest.raises(ValueError, match="unknown setting"):
        AuraConfig.load(tmp_path)


def test_defaults_when_missing(tmp_path):
    assert AuraConfig.load(tmp_path) == AuraConfig()


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "abc")
    assert get_token("github") == "abc"


def test_ignore_rules(repo_dir):
    (repo_dir / ".auraignore").write_text("# comment\ndata/\n*.csv\n")
    rules = IgnoreRules(repo_dir, extra=["secret.txt"])
    assert rules.is_ignored(repo_dir / ".git" / "index")
    assert rules.is_ignored(repo_dir / "pkg" / "__pycache__" / "m.pyc")
    assert rules.is_ignored(repo_dir / "data" / "x.json")
    assert rules.is_ignored(repo_dir / "report.csv")
    assert rules.is_ignored(repo_dir / "secret.txt")
    assert rules.is_ignored(repo_dir / "build" / "out.o")  # from .gitignore via git check-ignore
    assert not rules.is_ignored(repo_dir / "src" / "main.py")
    assert rules.is_ignored(repo_dir.parent / "elsewhere.py")


def test_watcher_debounces_bursts(repo_dir):
    fired = []
    done = threading.Event()

    def cb():
        fired.append(time.monotonic())
        done.set()

    watcher = RepoWatcher(repo_dir, cb, debounce_seconds=0.4)
    watcher.start()
    try:
        time.sleep(0.2)
        for i in range(5):
            (repo_dir / "file.txt").write_text(str(i))
            time.sleep(0.05)
        (repo_dir / "ignored.log").write_text("noise")
        assert done.wait(5)
        time.sleep(0.8)
        assert len(fired) == 1
    finally:
        watcher.stop()
    assert not watcher.running


def test_watcher_ignores_noise_only(repo_dir):
    fired = []
    watcher = RepoWatcher(repo_dir, lambda: fired.append(1), debounce_seconds=0.2)
    watcher.start()
    try:
        time.sleep(0.2)
        (repo_dir / "debug.log").write_text("x")
        (repo_dir / "build").mkdir()
        (repo_dir / "build" / "a.o").write_text("x")
        time.sleep(0.8)
    finally:
        watcher.stop()
    assert fired == []
