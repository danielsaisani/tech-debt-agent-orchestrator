"""Thin GitHub client (issues and comments) and webhook signature verification."""

import hashlib
import hmac

import httpx


class GitHubError(Exception):
    """GitHub rejected the request, or it could not be completed."""


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Check GitHub's X-Hub-Signature-256 header against the raw request body."""
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature_header.encode())


class GitHubClient:
    def __init__(self, token: str, base_url: str, http: httpx.AsyncClient):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._http = http

    async def get_issue(self, repo: str, number: int) -> dict | None:
        """Return the issue, or None when it does not exist (or is a pull request)."""
        response = await self._request("GET", f"/repos/{repo}/issues/{number}")
        if response.status_code == 404:
            return None
        self._raise_for_status(response)
        issue = response.json()
        # The issues API also returns pull requests; those are not remediation targets.
        return None if "pull_request" in issue else issue

    async def list_labeled_issues(self, repo: str, label: str) -> list[dict]:
        """Every open issue (not pull request) carrying the label."""
        issues, page = [], 1
        while True:
            response = await self._request(
                "GET",
                f"/repos/{repo}/issues",
                params={"labels": label, "state": "open", "per_page": 100, "page": page},
            )
            self._raise_for_status(response)
            batch = response.json()
            issues += [issue for issue in batch if "pull_request" not in issue]
            if len(batch) < 100:
                return issues
            page += 1

    async def create_comment(self, repo: str, number: int, body: str) -> None:
        response = await self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        )
        self._raise_for_status(response)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return await self._http.request(
                method, self._base_url + path, headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"{method} {path} failed: {exc!r}") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise GitHubError(f"GitHub returned {response.status_code}: {response.text[:500]}")
