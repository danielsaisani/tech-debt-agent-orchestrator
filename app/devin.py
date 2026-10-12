"""Thin client for the Devin Organization API v3, plus the session prompt."""

import httpx

PROMPT_TEMPLATE = """\
You are resolving a tech-debt issue in the GitHub repository {repo}.

Issue #{number}: {title}
{url}

--- Issue description ---
{body}
--- End of issue description ---

Instructions:
- Work only in {repo}. Make the smallest change that fully resolves the issue.
- Open exactly one pull request, and open it against {repo}. Never open a pull
  request against any other repository, including the upstream this repository
  was forked from.
- Reference the issue in the pull request description with "Closes #{number}".
- Run the relevant tests or linters for the code you touched before opening it.
- Do not wait for a reply: nobody is watching this session. If you cannot
  resolve the issue, stop and say why.
"""


class DevinError(Exception):
    """Devin rejected the request, or it could not be completed."""


class DevinTransientError(DevinError):
    """A failure worth retrying: network trouble, a 429 or a 5xx."""


def build_prompt(run: dict, target_repo: str) -> str:
    return PROMPT_TEMPLATE.format(
        repo=target_repo,
        number=run["issue_number"],
        title=run["issue_title"],
        url=run["issue_url"],
        body=run["issue_body"] or "(no description provided)",
    )


class DevinClient:
    def __init__(self, api_key: str, org_id: str, base_url: str, http: httpx.AsyncClient):
        self._sessions_url = f"{base_url.rstrip('/')}/organizations/{org_id}/sessions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http

    async def create_session(
        self, *, prompt: str, repos: list[str], title: str, tags: list[str], max_acu_limit: int
    ) -> dict:
        body = {
            "prompt": prompt,
            "repos": repos,
            "title": title,
            "tags": tags,
            "max_acu_limit": max_acu_limit,
        }
        return await self._request("POST", self._sessions_url, json=body)

    async def get_session(self, session_id: str) -> dict:
        # The path parameter is the session id prefixed with "devin-".
        devin_id = session_id if session_id.startswith("devin-") else f"devin-{session_id}"
        return await self._request("GET", f"{self._sessions_url}/{devin_id}")

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = await self._http.request(method, url, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DevinTransientError(f"{method} {url} failed: {exc!r}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise DevinTransientError(f"Devin returned {response.status_code}")
        if response.status_code >= 400:
            raise DevinError(f"Devin returned {response.status_code}: {response.text[:500]}")
        return response.json()
