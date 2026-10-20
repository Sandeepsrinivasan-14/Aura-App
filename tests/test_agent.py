from aura.agent import Agent
from aura.config import AuraConfig
from aura.git_ops import GitRepo
from aura.providers import Provider, PullRequest

from .conftest import git


class FakeProvider(Provider):
    kind = "github"
    git_username = "x-access-token"

    def __init__(self, existing=None):
        super().__init__("tok")
        self.created, self.merged, self.existing = [], [], existing

    def _headers(self):
        return {}

    def find_open_pull_request(self, head, base):
        return self.existing

    def create_pull_request(self, title, body, head, base):
        self.created.append((title, body, head, base))
        return PullRequest("1", "https://example.test/pr/1", title)

    def merge_pull_request(self, pr):
        self.merged.append(pr.id)


class LocalOnlyFake(FakeProvider):
    """Fake API, but pushes go to the local bare remote (no auth header needed)."""

    git_username = ""


def make_agent(repo_dir, provider=None, **cfg):
    repo = GitRepo(repo_dir)
    agent = Agent(repo, AuraConfig(**cfg), provider=provider)
    if provider is not None:
        # local bare remote ignores http headers; keep the provider for PR calls only
        agent.provider = lambda: provider
    return agent


def test_no_changes(repo_dir):
    assert make_agent(repo_dir).run_once().status == "no_changes"


def test_direct_mode_commits_and_pushes_current_branch(repo_dir, remote_dir):
    (repo_dir / "notes.md").write_text("hello\n")
    result = make_agent(repo_dir, mode="direct").run_once()
    assert result.status == "committed" and result.pushed and result.branch == "main"
    assert result.message.startswith("docs: add notes.md")
    assert git(remote_dir, "rev-parse", "main").strip() == result.sha
    assert result.pull_request is None


def test_review_can_edit_or_reject(repo_dir):
    (repo_dir / "a.py").write_text("x = 1\n")
    agent = make_agent(repo_dir, mode="direct", push=False)
    agent.review = lambda cs, msg: None
    assert agent.run_once().status == "skipped"
    assert GitRepo(repo_dir).is_dirty()

    agent.review = lambda cs, msg: "feat: my own message"
    result = agent.run_once()
    assert result.message == "feat: my own message"
    assert git(repo_dir, "log", "-1", "--format=%s").strip() == "feat: my own message"


def test_pull_request_mode_creates_work_branch_and_pr(repo_dir, remote_dir):
    provider = LocalOnlyFake()
    agent = make_agent(repo_dir, provider=provider)
    (repo_dir / "feature.py").write_text("def f():\n    return 1\n")
    result = agent.run_once()
    assert result.status == "committed"
    assert result.branch.startswith("aura/")
    assert git(remote_dir, "rev-parse", result.branch).strip() == result.sha
    assert git(remote_dir, "rev-parse", "main").strip() != result.sha  # base branch untouched
    assert provider.created[0][2:] == (result.branch, "main")
    assert result.pull_request.url == "https://example.test/pr/1"
    assert not provider.merged

    # Second change: same branch, existing PR is reused
    provider.existing = PullRequest("1", "https://example.test/pr/1", "t", existing=True)
    (repo_dir / "feature.py").write_text("def f():\n    return 2\n")
    second = agent.run_once()
    assert second.branch == result.branch
    assert second.pull_request.existing
    assert len(provider.created) == 1


def test_auto_merge(repo_dir):
    provider = LocalOnlyFake()
    agent = make_agent(repo_dir, provider=provider, auto_merge=True)
    (repo_dir / "x.py").write_text("1\n")
    assert agent.run_once().merged
    assert provider.merged == ["1"]


def test_pr_mode_without_token_still_pushes(repo_dir, remote_dir):
    agent = make_agent(repo_dir)
    agent.provider = lambda: None
    (repo_dir / "x.py").write_text("1\n")
    result = agent.run_once()
    assert result.pushed and result.pull_request is None
    assert any("no API token" in n for n in result.notes)


def test_cooldown(repo_dir):
    now = [1000.0]
    agent = make_agent(repo_dir, mode="direct", push=False, cooldown_seconds=60)
    agent.clock = lambda: now[0]
    (repo_dir / "a.txt").write_text("1\n")
    assert agent.run_once(respect_cooldown=True).status == "committed"
    (repo_dir / "a.txt").write_text("2\n")
    assert agent.run_once(respect_cooldown=True).status == "skipped"
    now[0] += 61
    assert agent.run_once(respect_cooldown=True).status == "committed"


def test_errors_are_reported_not_raised(repo_dir):
    git(repo_dir, "remote", "set-url", "origin", str(repo_dir / "missing.git"))
    (repo_dir / "a.txt").write_text("1\n")
    result = make_agent(repo_dir, mode="direct").run_once()
    assert result.status == "error"
    assert "push" in result.notes[0]


def test_no_remote_keeps_commit_local(repo_dir):
    git(repo_dir, "remote", "remove", "origin")
    (repo_dir / "a.txt").write_text("1\n")
    result = make_agent(repo_dir, mode="direct").run_once()
    assert result.status == "committed" and not result.pushed
