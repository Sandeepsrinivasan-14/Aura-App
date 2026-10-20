from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullRequest:
    id: str
    url: str
    title: str
    existing: bool = False  # True when an open PR for the branch already existed


class Provider(ABC):
    kind: str = ""
    git_username: str = ""  # username to pair with the token for HTTPS pushes

    def __init__(self, token: str, session: requests.Session | None = None, timeout: float = 30):
        if not token:
            raise ProviderError(f"no {self.kind} token configured")
        self.token = token
        self.session = session or requests.Session()
        self.timeout = timeout

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            resp = self.session.request(method, url, timeout=self.timeout, headers=self._headers(), **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"{self.kind} request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ProviderError(f"{self.kind} rejected the token (HTTP {resp.status_code}); check its scopes and expiry")
        return resp

    @staticmethod
    def _error(resp: requests.Response) -> str:
        try:
            body = resp.json()
            return str(body.get("message") or body.get("errors") or body)
        except ValueError:
            return resp.text[:300]

    @abstractmethod
    def _headers(self) -> dict[str, str]: ...

    @abstractmethod
    def find_open_pull_request(self, head: str, base: str) -> PullRequest | None: ...

    @abstractmethod
    def create_pull_request(self, title: str, body: str, head: str, base: str) -> PullRequest: ...

    @abstractmethod
    def merge_pull_request(self, pr: PullRequest) -> None: ...

    def open_or_reuse_pull_request(self, title: str, body: str, head: str, base: str) -> PullRequest:
        return self.find_open_pull_request(head, base) or self.create_pull_request(title, body, head, base)
