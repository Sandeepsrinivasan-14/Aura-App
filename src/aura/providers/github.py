from __future__ import annotations

from .base import Provider, ProviderError, PullRequest


class GitHubProvider(Provider):
    """GitHub REST API v3. Needs a fine-grained token with Contents and Pull requests read/write."""

    kind = "github"
    git_username = "x-access-token"

    def __init__(self, owner: str, repo: str, token: str, api_url: str = "https://api.github.com", **kwargs):
        super().__init__(token, **kwargs)
        self.owner, self.repo = owner, repo
        self.base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def find_open_pull_request(self, head: str, base: str) -> PullRequest | None:
        resp = self._request("GET", f"{self.base}/pulls", params={"state": "open", "head": f"{self.owner}:{head}", "base": base})
        if resp.status_code != 200:
            raise ProviderError(f"could not list pull requests: {self._error(resp)}")
        for pr in resp.json():
            return PullRequest(str(pr["number"]), pr["html_url"], pr["title"], existing=True)
        return None

    def create_pull_request(self, title: str, body: str, head: str, base: str) -> PullRequest:
        resp = self._request("POST", f"{self.base}/pulls", json={"title": title, "body": body, "head": head, "base": base})
        if resp.status_code != 201:
            raise ProviderError(f"could not create pull request: {self._error(resp)}")
        pr = resp.json()
        return PullRequest(str(pr["number"]), pr["html_url"], pr["title"])

    def merge_pull_request(self, pr: PullRequest) -> None:
        resp = self._request("PUT", f"{self.base}/pulls/{pr.id}/merge", json={"merge_method": "squash"})
        if resp.status_code != 200:
            raise ProviderError(f"could not merge pull request #{pr.id}: {self._error(resp)}")
