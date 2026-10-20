from __future__ import annotations

import base64
from urllib.parse import quote

from .base import Provider, ProviderError, PullRequest

API_VERSION = "7.1"


class AzureDevOpsProvider(Provider):
    """Azure DevOps Git REST API. Needs a PAT with the Code (Read & write) scope."""

    kind = "azure_devops"
    git_username = "aura"  # any non-empty username works with a PAT

    def __init__(self, organization: str, project: str, repo: str, token: str, api_url: str = "https://dev.azure.com", **kwargs):
        super().__init__(token, **kwargs)
        if not project:
            raise ProviderError("Azure DevOps remote URL is missing the project name")
        self.organization, self.project, self.repo = organization, project, repo
        self.base = f"{api_url.rstrip('/')}/{quote(organization)}/{quote(project)}/_apis/git/repositories/{quote(repo)}"
        self.web = f"{api_url.rstrip('/')}/{quote(organization)}/{quote(project)}/_git/{quote(repo)}"

    def _headers(self) -> dict[str, str]:
        auth = base64.b64encode(f":{self.token}".encode()).decode()
        return {"Authorization": f"Basic {auth}", "Content-Type": "application/json", "Accept": "application/json"}

    def _pr(self, data: dict, existing: bool = False) -> PullRequest:
        pr_id = str(data["pullRequestId"])
        return PullRequest(pr_id, f"{self.web}/pullrequest/{pr_id}", data.get("title", ""), existing)

    def find_open_pull_request(self, head: str, base: str) -> PullRequest | None:
        params = {
            "searchCriteria.status": "active",
            "searchCriteria.sourceRefName": f"refs/heads/{head}",
            "searchCriteria.targetRefName": f"refs/heads/{base}",
            "api-version": API_VERSION,
        }
        resp = self._request("GET", f"{self.base}/pullrequests", params=params)
        if resp.status_code != 200:
            raise ProviderError(f"could not list pull requests: {self._error(resp)}")
        items = resp.json().get("value", [])
        return self._pr(items[0], existing=True) if items else None

    def create_pull_request(self, title: str, body: str, head: str, base: str) -> PullRequest:
        payload = {
            "sourceRefName": f"refs/heads/{head}",
            "targetRefName": f"refs/heads/{base}",
            "title": title,
            "description": body[:4000],
        }
        resp = self._request("POST", f"{self.base}/pullrequests", params={"api-version": API_VERSION}, json=payload)
        if resp.status_code not in (200, 201):
            raise ProviderError(f"could not create pull request: {self._error(resp)}")
        return self._pr(resp.json())

    def merge_pull_request(self, pr: PullRequest) -> None:
        url = f"{self.base}/pullrequests/{pr.id}"
        current = self._request("GET", url, params={"api-version": API_VERSION})
        if current.status_code != 200:
            raise ProviderError(f"could not read pull request {pr.id}: {self._error(current)}")
        payload = {
            "status": "completed",
            "lastMergeSourceCommit": current.json()["lastMergeSourceCommit"],
            "completionOptions": {"mergeStrategy": "squash", "deleteSourceBranch": True},
        }
        resp = self._request("PATCH", url, params={"api-version": API_VERSION}, json=payload)
        if resp.status_code != 200:
            raise ProviderError(f"could not complete pull request {pr.id}: {self._error(resp)}")
