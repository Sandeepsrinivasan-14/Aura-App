"""Hosting providers (GitHub, Azure DevOps) for pull requests and authenticated pushes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .azure_devops import AzureDevOpsProvider
from .base import Provider, ProviderError, PullRequest
from .github import GitHubProvider

__all__ = [
    "AzureDevOpsProvider",
    "GitHubProvider",
    "Provider",
    "ProviderError",
    "PullRequest",
    "RemoteInfo",
    "parse_remote",
    "provider_for_remote",
]


@dataclass(frozen=True)
class RemoteInfo:
    kind: str  # "github" | "azure_devops"
    owner: str  # GitHub owner, or Azure DevOps organization
    repo: str
    project: str | None = None  # Azure DevOps only


_PATTERNS = [
    (
        "github",
        re.compile(
            r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
            r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
        ),
    ),
    (
        "azure_devops",
        re.compile(r"^https://(?:[^@/]+@)?dev\.azure\.com/(?P<owner>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+?)/?$"),
    ),
    (
        "azure_devops",
        re.compile(r"^(?:git@|ssh://git@)ssh\.dev\.azure\.com[:/]v3/(?P<owner>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+?)/?$"),
    ),
    (
        "azure_devops",
        re.compile(
            r"^https://(?P<owner>[^./@]+)\.visualstudio\.com/(?:DefaultCollection/)?(?P<project>[^/]+)/_git/(?P<repo>[^/]+?)/?$"
        ),
    ),
]


def parse_remote(url: str) -> RemoteInfo | None:
    for kind, pattern in _PATTERNS:
        m = pattern.match(url.strip())
        if m:
            d = m.groupdict()
            return RemoteInfo(kind, d["owner"], d["repo"], d.get("project"))
    return None


def provider_for_remote(url: str, token: str, **kwargs) -> Provider:
    info = parse_remote(url)
    if info is None:
        raise ProviderError(f"unsupported remote {url!r}: only GitHub and Azure DevOps are supported")
    if info.kind == "github":
        return GitHubProvider(info.owner, info.repo, token, **kwargs)
    return AzureDevOpsProvider(info.owner, info.project or "", info.repo, token, **kwargs)
