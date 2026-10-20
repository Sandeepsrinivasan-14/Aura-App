import pytest

from aura.git_ops import GitError, GitRepo, basic_auth_header

from .conftest import git


def test_rejects_non_repo(tmp_path):
    with pytest.raises(GitError, match="not inside a Git repository"):
        GitRepo(tmp_path)


def test_changes_include_untracked_modified_deleted_and_respect_gitignore(repo_dir):
    (repo_dir / "README.md").write_text("# demo\nmore\n")
    (repo_dir / "new.py").write_text("print('hi')\n")
    (repo_dir / "build").mkdir()
    (repo_dir / "build" / "out.bin").write_bytes(b"\0")
    (repo_dir / ".gitignore").unlink()
    repo = GitRepo(repo_dir)
    kinds = {c.path: c.kind for c in repo.changes()}
    assert kinds["README.md"] == "modified"
    assert kinds["new.py"] == "added"
    assert kinds[".gitignore"] == "deleted"


def test_preview_does_not_touch_the_users_index(repo_dir):
    (repo_dir / "a.txt").write_text("one\n")
    (repo_dir / "b.txt").write_text("two\n")
    git(repo_dir, "add", "a.txt")  # user staged only a.txt
    repo = GitRepo(repo_dir)
    diff = repo.diff()
    assert "a.txt" in diff and "b.txt" in diff
    stats = {s.path: (s.added, s.deleted) for s in repo.diff_stats()}
    assert stats["b.txt"] == (1, 0)
    staged = git(repo_dir, "diff", "--cached", "--name-only").split()
    assert staged == ["a.txt"]


def test_commit_all_and_push(repo_dir, remote_dir):
    (repo_dir / "x.txt").write_text("x\n")
    repo = GitRepo(repo_dir)
    sha = repo.commit_all("feat: add x\n\nbody line")
    assert len(sha) == 40
    assert not repo.is_dirty()
    assert git(repo_dir, "log", "-1", "--format=%B").strip() == "feat: add x\n\nbody line"
    repo.push("main")
    assert git(remote_dir, "rev-parse", "main").strip() == sha


def test_empty_message_rejected(repo_dir):
    (repo_dir / "x.txt").write_text("x\n")
    with pytest.raises(GitError, match="empty"):
        GitRepo(repo_dir).commit_all("   ")


def test_branch_helpers(repo_dir):
    repo = GitRepo(repo_dir)
    assert repo.current_branch() == "main"
    repo.create_branch("aura/test")
    assert repo.current_branch() == "aura/test"
    assert repo.branch_exists("main")
    with pytest.raises(GitError):
        repo.create_branch("bad..name")


def test_push_auth_header_is_not_persisted_and_is_redacted(repo_dir):
    repo = GitRepo(repo_dir)
    git(repo_dir, "remote", "set-url", "origin", "https://127.0.0.1:9/nowhere.git")
    header = basic_auth_header("x-access-token", "supersecret")
    with pytest.raises(GitError) as err:
        repo.push("main", auth_header=header)
    assert header.split()[-1] not in str(err.value)
    assert "supersecret" not in (repo_dir / ".git" / "config").read_text()


def test_initial_commit_in_empty_repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_text("hello\n")
    repo = GitRepo(tmp_path)
    assert not repo.has_commits()
    assert "f.txt" in repo.diff()
    repo.commit_all("chore: initial commit")
    assert repo.has_commits()
