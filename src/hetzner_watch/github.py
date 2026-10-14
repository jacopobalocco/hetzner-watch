from __future__ import annotations

import httpx

API_URL = "https://api.github.com"
STATE_OPEN = "open"
STATE_CLOSED = "closed"


class Issues:
    def __init__(
        self, repo: str, token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._repo = repo
        self._client = httpx.Client(
            base_url=API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
            transport=transport,
        )

    def __enter__(self) -> Issues:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        response = self._client.request(method, f"/repos/{self._repo}{path}", **kwargs)
        response.raise_for_status()
        return response

    def find_open(self, label: str) -> int | None:
        response = self._request(
            "GET", "/issues", params={"state": STATE_OPEN, "labels": label}
        )
        found = response.json()
        return int(found[0]["number"]) if found else None

    def create(self, title: str, body: str, label: str) -> int:
        response = self._request(
            "POST", "/issues", json={"title": title, "body": body, "labels": [label]}
        )
        return int(response.json()["number"])

    def update(self, number: int, body: str) -> None:
        self._request("PATCH", f"/issues/{number}", json={"body": body})

    def close(self, number: int, comment: str) -> None:
        # Comment first, then flip state: a close comment posted after the
        # issue is already closed reads (and notifies) as attached to a
        # closed issue, which looks wrong even though both calls succeed.
        self._request("POST", f"/issues/{number}/comments", json={"body": comment})
        self._request("PATCH", f"/issues/{number}", json={"state": STATE_CLOSED})
