"""PySide6 desktop app: pick a repository, watch it, and review every commit before it happens."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .agent import Agent, RunResult
from .commit_message import ChangeSet, make_writer
from .config import AuraConfig
from .git_ops import GitError, GitRepo
from .ignore import IgnoreRules
from .style import STYLESHEET, AuraOrb, paint_aurora
from .watcher import RepoWatcher

log = logging.getLogger(__name__)


class DiffHighlighter(QSyntaxHighlighter):
    """Colours a unified diff: additions, removals, hunk headers and file headers."""

    def __init__(self, document):
        super().__init__(document)

        def fmt(color: str, bold: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f

        self.add, self.rem = fmt("#4FF0C4"), fmt("#FF8FB1")
        self.hunk, self.head = fmt("#8B7BFF", True), fmt("#9A9CC0", True)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        if text.startswith(("+++", "---", "diff --git", "index ", "new file", "deleted file")):
            self.setFormat(0, len(text), self.head)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.hunk)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.rem)


class ReviewDialog(QDialog):
    def __init__(self, cs: ChangeSet, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review commit")
        self.resize(900, 650)
        self.setStyleSheet(STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        heading = QLabel("Review commit")
        heading.setObjectName("title")
        summary = QLabel(f"{len(cs.changes)} file(s) changed, +{cs.lines_added}/-{cs.lines_deleted}. Edit the message if needed.")
        summary.setObjectName("subtitle")
        layout.addWidget(heading)
        layout.addWidget(summary)
        self.message = QPlainTextEdit(message)
        self.message.setMaximumHeight(140)
        layout.addWidget(self.message)
        diff = QTextEdit()
        diff.setReadOnly(True)
        diff.setFont(QFont("Consolas" if sys.platform == "win32" else "Monospace", 9))
        diff.setPlainText(cs.diff or "(no textual diff)")
        self._highlighter = DiffHighlighter(diff.document())
        layout.addWidget(diff, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Commit")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        paint_aurora(self)
        super().paintEvent(event)

    def result_message(self) -> str | None:
        return self.message.toPlainText().strip() or None


class Worker(QObject):
    """Runs the agent off the UI thread; asks the UI thread to show the review dialog."""

    review_requested = Signal(object, str)
    finished = Signal(object)

    def __init__(self, agent: Agent):
        super().__init__()
        self.agent = agent
        self._answer: str | None = None
        self._answer_ready = None
        agent.review = self._review

    def _review(self, cs: ChangeSet, message: str) -> str | None:
        from threading import Event

        self._answer_ready = Event()
        self.review_requested.emit(cs, message)
        self._answer_ready.wait()
        return self._answer

    def answer(self, message: str | None) -> None:
        self._answer = message
        if self._answer_ready:
            self._answer_ready.set()

    @Slot()
    def run(self) -> None:
        self.finished.emit(self.agent.run_once())


class AuraWindow(QWidget):
    settled = Signal()

    def __init__(self, path: str | None = None):
        super().__init__()
        self.setWindowTitle(f"Aura {__version__}")
        self.resize(820, 620)
        self.repo: GitRepo | None = None
        self.agent: Agent | None = None
        self.watcher: RepoWatcher | None = None
        self._thread: QThread | None = None

        self.setObjectName("root")
        self.setMinimumSize(720, 560)
        self.setStyleSheet(STYLESHEET)

        # Header: orb, title, repository and live status
        self.orb = AuraOrb(72)
        title = QLabel("Aura")
        title.setObjectName("title")
        self.folder = QLabel("No repository selected")
        self.folder.setObjectName("subtitle")
        self.folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel("Choose a repository to begin")
        self.status.setObjectName("status")
        choose = QPushButton("Choose repository…")
        choose.clicked.connect(self.choose_folder)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(title)
        heading.addWidget(self.folder)
        heading.addWidget(self.status)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(self.orb)
        header.addLayout(heading, 1)
        header.addWidget(choose, 0, Qt.AlignTop)

        # Settings card
        self.mode = QComboBox()
        self.mode.addItems(["pull_request", "direct"])
        self.mode.setToolTip("pull_request: work on an aura/ branch and open a PR. direct: commit to the current branch.")
        self.auto_merge = QCheckBox("Auto-merge PRs")
        self.ai = QComboBox()
        self.ai.addItems(["rules", "ollama"])
        self.ai.setToolTip("rules: offline message writer. ollama: local LLM with automatic fallback.")
        save = QPushButton("Save settings")
        save.clicked.connect(self.save_settings)
        settings = QHBoxLayout()
        settings.setSpacing(12)
        for label, w in (("Workflow", self.mode), ("Messages", self.ai)):
            col = QVBoxLayout()
            col.setSpacing(4)
            cap = QLabel(label)
            cap.setProperty("class", "section")
            col.addWidget(cap)
            col.addWidget(w)
            settings.addLayout(col)
        settings.addWidget(self.auto_merge, 0, Qt.AlignBottom)
        settings.addStretch(1)
        settings.addWidget(save, 0, Qt.AlignBottom)
        settings_card = self._card(settings)

        # Actions
        self.watch_btn = QPushButton("Start watching")
        self.watch_btn.setObjectName("primary")
        self.watch_btn.setCheckable(True)
        self.watch_btn.setMinimumHeight(40)
        self.watch_btn.toggled.connect(self.toggle_watch)
        self.commit_btn = QPushButton("Review && commit now")
        self.commit_btn.setMinimumHeight(40)
        self.commit_btn.clicked.connect(self.commit_now)
        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addWidget(self.watch_btn, 1)
        actions.addWidget(self.commit_btn, 1)

        # Activity card
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Commits, pushes and pull requests will appear here.")
        activity = QVBoxLayout()
        cap = QLabel("Activity")
        cap.setProperty("class", "section")
        activity.addWidget(cap)
        activity.addWidget(self.log_view, 1)
        activity_card = self._card(activity)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(settings_card)
        layout.addLayout(actions)
        layout.addWidget(activity_card, 1)

        self.settled.connect(self.commit_now)
        self._set_enabled(False)
        if path:
            self.open_repo(path)

    # ---------- helpers ----------

    @staticmethod
    def _card(inner) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        inner.setContentsMargins(18, 14, 18, 16)
        frame.setLayout(inner)
        return frame

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        paint_aurora(self)
        super().paintEvent(event)

    def set_status(self, text: str, state: str) -> None:
        self.status.setText(text)
        self.orb.set_state(state)

    def log(self, text: str) -> None:
        from datetime import datetime

        self.log_view.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")

    def _set_enabled(self, on: bool) -> None:
        for w in (self.mode, self.auto_merge, self.ai, self.watch_btn, self.commit_btn):
            w.setEnabled(on)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a Git repository")
        if path:
            self.open_repo(path)

    def open_repo(self, path: str) -> None:
        try:
            self.repo = GitRepo(path)
            cfg = AuraConfig.load(self.repo.path)
            self.agent = Agent(self.repo, cfg, writer=make_writer(cfg.ai_backend, cfg.ollama_url, cfg.ollama_model))
        except (GitError, ValueError) as exc:
            QMessageBox.warning(self, "Aura", str(exc))
            return
        self.folder.setText(f"{self.repo.path}  (branch {self.repo.current_branch()})")
        self.set_status("Ready", "idle")
        self.mode.setCurrentText(cfg.mode)
        self.auto_merge.setChecked(cfg.auto_merge)
        self.ai.setCurrentText(cfg.ai_backend)
        self._set_enabled(True)
        self.log(f"Opened {self.repo.path}")

    def save_settings(self) -> None:
        if not self.agent:
            return
        cfg = self.agent.config
        cfg.mode, cfg.auto_merge, cfg.ai_backend = self.mode.currentText(), self.auto_merge.isChecked(), self.ai.currentText()
        cfg.save(self.repo.path)
        self.agent.writer = make_writer(cfg.ai_backend, cfg.ollama_url, cfg.ollama_model)
        self.log("Settings saved to .aura.json")

    # ---------- actions ----------

    def toggle_watch(self, on: bool) -> None:
        if on and self.agent:
            rules = IgnoreRules(self.repo.path, self.agent.config.ignore)
            self.watcher = RepoWatcher(self.repo.path, self.settled.emit, self.agent.config.debounce_seconds, rules)
            self.watcher.start()
            self.watch_btn.setText("Stop watching")
            self.set_status("Watching for changes", "watching")
            self.log("Watching for changes…")
        elif self.watcher:
            self.watcher.stop()
            self.watcher = None
            self.watch_btn.setText("Start watching")
            self.set_status("Ready", "idle")
            self.log("Stopped watching")

    def commit_now(self) -> None:
        if not self.agent or self._thread is not None:
            return
        self.set_status("Preparing a commit…", "working")
        self._thread = QThread(self)
        self._worker = Worker(self.agent)
        self._worker.moveToThread(self._thread)
        self._worker.review_requested.connect(self._show_review)
        self._worker.finished.connect(self._done)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    @Slot(object, str)
    def _show_review(self, cs: ChangeSet, message: str) -> None:
        dlg = ReviewDialog(cs, message, self)
        self._worker.answer(dlg.result_message() if dlg.exec() == QDialog.Accepted else None)

    @Slot(object)
    def _done(self, result: RunResult) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        resting = ("Watching for changes", "watching") if self.watcher else ("Ready", "idle")
        self.set_status(*resting)
        if result.status == "committed":
            self.log(f"Committed {result.sha[:8]} on {result.branch}: {result.message.splitlines()[0]}")
            if result.pushed:
                self.log(f"Pushed {result.branch}")
            if result.pull_request:
                self.log(f"Pull request: {result.pull_request.url}")
            for note in result.notes:
                self.log(f"Note: {note}")
        elif result.status == "error":
            self.set_status("Last action failed; see Activity", "error")
            self.log("Error: " + "; ".join(result.notes))
        elif result.status == "skipped":
            self.log("Skipped: " + "; ".join(result.notes))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.watcher:
            self.watcher.stop()
        super().closeEvent(event)


def run(path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = AuraWindow(str(Path(path).resolve()) if path else None)
    window.show()
    return app.exec()
