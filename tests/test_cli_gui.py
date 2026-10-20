import os
import time

import pytest

from aura.cli import main

from .conftest import git


def test_cli_init_status_commit(repo_dir, remote_dir, capsys, monkeypatch):
    monkeypatch.chdir(repo_dir)
    assert main(["init", "--mode", "direct"]) == 0
    assert (repo_dir / ".aura.json").exists()
    assert main(["init"]) == 1  # refuses to overwrite

    (repo_dir / "app.py").write_text("print(1)\n")
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Mode       : direct" in out and "app.py" in out

    assert main(["commit", "--dry-run"]) == 0
    assert "feat: add 2 files" in capsys.readouterr().out
    assert GitRepo_dirty(repo_dir)

    assert main(["commit", "-y", "-m", "feat: add app"]) == 0
    out = capsys.readouterr().out
    assert "Committed" in out and "Pushed main" in out
    assert git(remote_dir, "log", "-1", "--format=%s", "main").strip() == "feat: add app"


def GitRepo_dirty(path) -> bool:  # noqa: N802
    return bool(git(path, "status", "--porcelain").strip())


def test_cli_interactive_review(repo_dir, monkeypatch, capsys):
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".aura.json").write_text('{"mode": "direct", "push": false}')
    (repo_dir / "a.py").write_text("x\n")
    answers = iter(["e", "fix: handle edge case"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main(["commit"]) == 0
    assert git(repo_dir, "log", "-1", "--format=%s").strip() == "fix: handle edge case"


def test_cli_errors_are_friendly(tmp_path, capsys):
    assert main(["status", str(tmp_path)]) == 1
    assert "not inside a Git repository" in capsys.readouterr().err


def test_gui_smoke(repo_dir, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from aura.gui import AuraWindow

    app = QApplication.instance() or QApplication([])
    (repo_dir / ".aura.json").write_text('{"mode": "direct", "push": false}')
    win = AuraWindow(str(repo_dir))
    assert "branch main" in win.folder.text()
    assert win.commit_btn.isEnabled()
    win.mode.setCurrentText("pull_request")
    win.save_settings()
    assert '"pull_request"' in (repo_dir / ".aura.json").read_text()
    win.close()
    app.processEvents()
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_gui_commit_flow_with_review(repo_dir, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog

    from aura import gui

    app = QApplication.instance() or QApplication([])
    (repo_dir / ".aura.json").write_text('{"mode": "direct", "push": false}')
    git(repo_dir, "add", ".aura.json")
    git(repo_dir, "commit", "-q", "-m", "chore: config")
    (repo_dir / "new_module.py").write_text("VALUE = 1\n")

    seen = {}

    def fake_exec(self):
        seen["message"] = self.message.toPlainText()
        self.message.setPlainText("feat: reviewed in gui")
        return QDialog.Accepted

    monkeypatch.setattr(gui.ReviewDialog, "exec", fake_exec)
    win = gui.AuraWindow(str(repo_dir))
    win.commit_now()
    deadline = time.monotonic() + 10
    while win._thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert seen["message"].startswith("feat: add new_module.py")
    assert git(repo_dir, "log", "-1", "--format=%s").strip() == "feat: reviewed in gui"
    assert "Committed" in win.log_view.toPlainText()
    win.close()
