"""File-system watcher with debouncing: many rapid saves become one callback after a quiet period."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .ignore import IgnoreRules

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: RepoWatcher):
        self.watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed", "closed_no_write"):
            return
        paths = [event.src_path, getattr(event, "dest_path", "")]
        if any(p and not self.watcher.rules.is_ignored(p) for p in paths):
            self.watcher.poke()


class RepoWatcher:
    def __init__(
        self,
        repo_path: str | Path,
        on_settled: Callable[[], None],
        debounce_seconds: float = 10.0,
        rules: IgnoreRules | None = None,
    ):
        self.root = Path(repo_path).resolve()
        self.on_settled = on_settled
        self.debounce = debounce_seconds
        self.rules = rules or IgnoreRules(self.root)
        self._observer = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def poke(self) -> None:
        """Register activity; the callback fires once no activity has been seen for `debounce` seconds."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self.on_settled()
        except Exception:  # never let a callback error kill the watcher thread
            log.exception("watcher callback failed")

    def start(self) -> None:
        if self._observer is not None:
            return
        self._observer = Observer()
        self._observer.schedule(_Handler(self), str(self.root), recursive=True)
        self._observer.start()
        log.info("watching %s", self.root)

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    @property
    def running(self) -> bool:
        return self._observer is not None
