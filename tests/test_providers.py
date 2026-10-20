import json

import pytest
import responses

from aura.providers import AzureDevOpsProvider, GitHubProvider, ProviderError, parse_remote, provider_for_remote


@pytest.mark.parametrize(
    ("url", "kind", "owner", "project", "repo"),
    [
        ("https://github.com/Sandeep/aura.git", "github", "Sandeep", None, "aura"),
        ("git@github.com:Sandeep/aura.git", "github", "Sandeep", None, "aura"),
        ("https://token@github.com/Sandeep/aura", "github", "Sandeep", None, "aura"),
        ("https://dev.azure.com/org/Proj/_git/repo", "azure_devops", "org", "Proj", "repo"),
        ("https://org@dev.azure.com/org/Proj/_git/repo", "azure_devops", "org", "Proj", "repo"),
        ("git@ssh.dev.azure.com:v3/org/Proj/repo", "azure_devops", "org", "Proj", "repo"),
        ("https://org.visualstudio.com/Proj/_git/repo", "azure_devops", "org", "Proj", "repo"),
    ],
)
def test_parse_remote(url, kind, owner, project, repo):
    info = parse_remote(url)
    assert (info.kind, info.owner, info.project, info.repo) == (kind, owner, project, repo)


def test_unsupported_remote():
    assert parse_remote("https://gitlab.com/a/b.git") is None
    with pytest.raises(ProviderError):
        provider_for_remote("https://gitlab.com/a/b.git", "t")


def test_token_required():
    with pytest.raises(ProviderError, match="no github token"):
        GitHubProvider("o", "r", "")


@responses.activate
def test_github_create_reuse_and_merge():
    gh = GitHubProvider("me", "proj", "tok")
    base = "https://api.github.com/repos/me/proj"
    responses.get(f"{base}/pulls", json=[])
    responses.post(f"{base}/pulls", status=201, json={"number": 7, "html_url": "https://github.com/me/proj/pull/7", "title": "t"})
    pr = gh.open_or_reuse_pull_request("t", "b", "aura/x", "main")
    assert (pr.id, pr.existing) == ("7", False)
    assert responses.calls[0].request.params == {"state": "open", "head": "me:aura/x", "base": "main"}
    assert responses.calls[1].request.headers["Authorization"] == "Bearer tok"

    responses.replace(responses.GET, f"{base}/pulls", json=[{"number": 7, "html_url": "u", "title": "t"}])
    assert gh.open_or_reuse_pull_request("t", "b", "aura/x", "main").existing is True

    responses.put(f"{base}/pulls/7/merge", json={"merged": True})
    gh.merge_pull_request(pr)
    assert json.loads(responses.calls[-1].request.body) == {"merge_method": "squash"}


@responses.activate
def test_github_errors_are_readable():
    gh = GitHubProvider("me", "proj", "tok")
    responses.get("https://api.github.com/repos/me/proj/pulls", status=401, json={"message": "Bad credentials"})
    with pytest.raises(ProviderError, match="rejected the token"):
        gh.find_open_pull_request("a", "main")
    responses.replace(responses.GET, "https://api.github.com/repos/me/proj/pulls", json=[])
    responses.post("https://api.github.com/repos/me/proj/pulls", status=422, json={"message": "No commits between main and a"})
    with pytest.raises(ProviderError, match="No commits between"):
        gh.open_or_reuse_pull_request("t", "b", "a", "main")


@responses.activate
def test_azure_create_and_complete():
    az = AzureDevOpsProvider("org", "Proj", "repo", "pat")
    base = "https://dev.azure.com/org/Proj/_apis/git/repositories/repo/pullrequests"
    responses.get(base, json={"value": []})
    responses.post(base, status=201, json={"pullRequestId": 12, "title": "t"})
    pr = az.open_or_reuse_pull_request("t", "body", "aura/x", "main")
    assert pr.url == "https://dev.azure.com/org/Proj/_git/repo/pullrequest/12"
    sent = json.loads(responses.calls[1].request.body)
    assert sent["sourceRefName"] == "refs/heads/aura/x" and sent["targetRefName"] == "refs/heads/main"
    assert responses.calls[1].request.headers["Authorization"].startswith("Basic ")

    responses.get(f"{base}/12", json={"lastMergeSourceCommit": {"commitId": "abc"}})
    responses.patch(f"{base}/12", json={"status": "completed"})
    az.merge_pull_request(pr)
    body = json.loads(responses.calls[-1].request.body)
    assert body["status"] == "completed" and body["lastMergeSourceCommit"] == {"commitId": "abc"}
