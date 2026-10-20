import requests
import responses

from aura.commit_message import ChangeSet, OllamaWriter, RuleBasedWriter
from aura.git_ops import Change, DiffStat


def cs(*entries, diff=""):
    changes = [Change(status, path) for status, path, *_ in entries]
    stats = [DiffStat(path, *(rest or [1, 0])) for _, path, *rest in entries]
    return ChangeSet(changes, stats, diff)


def summary(message: str) -> str:
    return message.splitlines()[0]


def test_docs_only():
    assert summary(RuleBasedWriter().write(cs((" M", "README.md")))) == "docs: update README.md"


def test_tests_only_with_scope():
    msg = RuleBasedWriter().write(cs(("??", "tests/test_api.py"), (" M", "tests/conftest.py")))
    assert summary(msg) == "test(tests): add 2 files"


def test_new_code_is_feat_with_scope():
    assert summary(RuleBasedWriter().write(cs(("??", "billing/invoice.py")))) == "feat(billing): add invoice.py"


def test_fix_keyword_in_added_lines():
    diff = "+++ b/app.py\n+    # fix crash when list is empty\n"
    assert summary(RuleBasedWriter().write(cs((" M", "app.py", 3, 1), diff=diff))).startswith("fix: fix app.py")


def test_fix_keyword_only_in_removed_lines_is_not_fix():
    diff = "--- a/app.py\n-    raise BugError()\n+    return None\n"
    assert not summary(RuleBasedWriter().write(cs((" M", "app.py", 1, 1), diff=diff))).startswith("fix")


def test_ci_and_build():
    assert summary(RuleBasedWriter().write(cs((" M", ".github/workflows/ci.yml")))).startswith("ci")
    assert summary(RuleBasedWriter().write(cs((" M", "requirements.txt")))).startswith("build")


def test_body_lists_files_and_stats():
    msg = RuleBasedWriter().write(cs((" M", "a.py", 5, 2), (" D", "b.py", 0, 9)))
    assert "2 file(s), +5/-11" in msg
    assert "- deleted b.py" in msg


def test_summary_line_length_capped():
    long_name = "x" * 120 + ".py"
    assert len(summary(RuleBasedWriter().write(cs(("??", long_name))))) <= 72


def test_clean_accepts_valid_and_rejects_junk():
    assert OllamaWriter.clean("Feat(api): add login endpoint.") == "feat(api): add login endpoint"
    assert OllamaWriter.clean("Commit message: fix: handle empty input") == "fix: handle empty input"
    assert OllamaWriter.clean("Sure! Here is a commit message for you") is None
    assert OllamaWriter.clean("") is None


@responses.activate
def test_ollama_used_when_valid():
    responses.post("http://localhost:11434/api/generate", json={"response": "feat(auth): add token refresh\n"})
    writer = OllamaWriter()
    msg = writer.write(cs((" M", "auth.py")))
    assert summary(msg) == "feat(auth): add token refresh"
    assert writer.last_source == "ollama"
    assert "1 file(s)" in msg  # rule-based body is kept


@responses.activate
def test_ollama_falls_back_on_error_and_on_bad_output():
    responses.post("http://localhost:11434/api/generate", body=requests.ConnectionError("down"))
    writer = OllamaWriter()
    assert summary(writer.write(cs((" M", "README.md")))) == "docs: update README.md"
    assert writer.last_source == "rules"

    responses.replace(responses.POST, "http://localhost:11434/api/generate", json={"response": "I changed stuff"})
    assert summary(writer.write(cs((" M", "README.md")))) == "docs: update README.md"
