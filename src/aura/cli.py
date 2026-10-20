"""Command-line interface.

aura init                 create .aura.json with defaults in the current repository
aura status               show branch, pending changes, remote and token status
aura commit [-y] [-m MSG] review and commit the current changes once (push / PR per config)
aura watch [--auto]       watch the repository and propose commits when edits settle
aura login github         store a token in the OS keychain
aura gui                  open the desktop app
"""

from __future__ import annotations

import argparse
import getpass
import logging
import signal
import sys
import threading

from . import __version__
from .agent import Agent, RunResult
from .commit_message import ChangeSet, make_writer
from .config import CONFIG_FILE, AuraConfig, get_token, set_token
from .git_ops import GitError, GitRepo
from .ignore import IgnoreRules
from .providers import parse_remote
from .watcher import RepoWatcher


def _print_changes(cs: ChangeSet) -> None:
    stats = {s.path: s for s in cs.stats}
    for c in cs.changes[:40]:
        s = stats.get(c.path)
        delta = "binary" if s and s.binary else f"+{s.added}/-{s.deleted}" if s else ""
        print(f"  {c.kind:9s} {c.path}  {delta}")
    if len(cs.changes) > 40:
        print(f"  ... and {len(cs.changes) - 40} more")


def terminal_review(cs: ChangeSet, message: str) -> str | None:
    print(f"\n{len(cs.changes)} changed file(s):")
    _print_changes(cs)
    print("\nProposed commit message:\n" + "\n".join(f"  | {line}" for line in message.splitlines()))
    while True:
        answer = input("\nCommit? [Y]es / [e]dit summary / [d]iff / [n]o: ").strip().lower()
        if answer in ("", "y", "yes"):
            return message
        if answer in ("n", "no"):
            return None
        if answer in ("d", "diff"):
            print(cs.diff or "(no textual diff)")
        if answer in ("e", "edit"):
            summary = input("New summary line: ").strip()
            if summary:
                rest = message.split("\n", 1)[1] if "\n" in message else ""
                return f"{summary}\n{rest}" if rest else summary


def _report(result: RunResult) -> int:
    if result.status == "no_changes":
        print("Nothing to commit.")
    elif result.status == "skipped":
        print("Skipped: " + "; ".join(result.notes))
    elif result.status == "error":
        print("Error: " + "; ".join(result.notes), file=sys.stderr)
        return 1
    else:
        print(f"Committed {result.sha[:8]} on {result.branch}: {result.message.splitlines()[0]}")
        if result.pushed:
            print(f"Pushed {result.branch} to origin.")
        if result.pull_request:
            verb = "Updated" if result.pull_request.existing else "Opened"
            print(f"{verb} pull request: {result.pull_request.url}")
        if result.merged:
            print("Pull request merged.")
        for note in result.notes:
            print(f"Note: {note}")
    return 0


def _agent(path: str, review=terminal_review) -> Agent:
    repo = GitRepo(path)
    cfg = AuraConfig.load(repo.path)
    return Agent(repo, cfg, writer=make_writer(cfg.ai_backend, cfg.ollama_url, cfg.ollama_model), review=review)


def cmd_init(args) -> int:
    repo = GitRepo(args.path)
    target = repo.path / CONFIG_FILE
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)")
        return 1
    cfg = AuraConfig(mode=args.mode, base_branch=args.base or "")
    print(f"Wrote {cfg.save(repo.path)}")
    return 0


def cmd_status(args) -> int:
    repo = GitRepo(args.path)
    cfg = AuraConfig.load(repo.path)
    url = repo.remote_url()
    info = parse_remote(url) if url else None
    print(f"Repository : {repo.path}")
    print(f"Branch     : {repo.current_branch()}")
    print(f"Mode       : {cfg.mode}" + (" (auto-merge on)" if cfg.auto_merge else ""))
    print(f"Remote     : {url or 'none'}" + (f" [{info.kind}]" if info else ""))
    if info:
        print(f"API token  : {'found' if get_token(info.kind) else 'missing (aura login ' + info.kind + ')'}")
    print(f"AI backend : {cfg.ai_backend}")
    changes = repo.changes()
    print(f"Changes    : {len(changes)}")
    if changes:
        _print_changes(ChangeSet(changes, repo.diff_stats()))
    return 0


def cmd_commit(args) -> int:
    review = (lambda cs, msg: args.message or msg) if args.yes else terminal_review
    if args.message and not args.yes:
        review = lambda cs, msg: terminal_review(cs, args.message)  # noqa: E731
    agent = _agent(args.path, review)
    if args.dry_run:
        cs = agent.changeset()
        if not cs.changes:
            print("Nothing to commit.")
            return 0
        _print_changes(cs)
        print("\n" + (args.message or agent.writer.write(cs)))
        return 0
    return _report(agent.run_once())


def cmd_watch(args) -> int:
    review = (lambda cs, msg: msg) if args.auto else terminal_review
    agent = _agent(args.path, review)
    busy = threading.Lock()

    def on_settled():
        if not busy.acquire(blocking=False):
            return
        try:
            result = agent.run_once(respect_cooldown=args.auto)
            if result.status != "no_changes":
                _report(result)
        finally:
            busy.release()

    rules = IgnoreRules(agent.repo.path, agent.config.ignore)
    watcher = RepoWatcher(agent.repo.path, on_settled, agent.config.debounce_seconds, rules)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    watcher.start()
    print(f"Watching {agent.repo.path} (mode: {agent.config.mode}{', auto' if args.auto else ''}). Ctrl+C to stop.")
    try:
        while not stop.wait(0.5):
            pass
    finally:
        watcher.stop()
    print("Stopped.")
    return 0


def cmd_login(args) -> int:
    token = getpass.getpass(f"{args.provider} token (input hidden): ").strip()
    if not token:
        print("No token entered.")
        return 1
    set_token(args.provider, token)
    print(f"Stored {args.provider} token in the OS keychain.")
    return 0


def cmd_gui(args) -> int:
    try:
        from .gui import run
    except ImportError as exc:
        print(f"The desktop app needs PySide6: pip install 'aura-git[gui]' ({exc})", file=sys.stderr)
        return 1
    return run(args.path)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aura", description="Local Git assistant: commit messages, pushes and pull requests.")
    p.add_argument("--version", action="version", version=f"aura {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create .aura.json")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--mode", choices=["direct", "pull_request"], default="pull_request")
    s.add_argument("--base", help="target branch for pull requests")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("status", help="show repository status")
    s.add_argument("path", nargs="?", default=".")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("commit", help="commit current changes once")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("-y", "--yes", action="store_true", help="skip the review prompt")
    s.add_argument("-m", "--message", help="use this message instead of a generated one")
    s.add_argument("--dry-run", action="store_true", help="show what would be committed")
    s.set_defaults(func=cmd_commit)

    s = sub.add_parser("watch", help="watch and propose commits")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--auto", action="store_true", help="commit without asking (respects cooldown)")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("login", help="store an API token in the OS keychain")
    s.add_argument("provider", choices=["github", "azure_devops"])
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("gui", help="open the desktop app")
    s.add_argument("path", nargs="?", default=None)
    s.set_defaults(func=cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (GitError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
