import json

import httpx
import pytest

from hetzner_watch.github import Issues

# Distinct from each other and from any JSON field name, so a value landing
# in the wrong slot (label swapped for body, id read instead of number, ...)
# cannot coincidentally satisfy an assertion.
REPO = "me/infra-fleet"
LABEL = "hetzner-watch"


def recorder(responses: list[httpx.Response]):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.read()
        payload = json.loads(raw) if raw else None
        calls.append((request.method, request.url.path, dict(request.url.params), payload))
        return responses.pop(0)

    return handler, calls


def issues(handler) -> Issues:
    return Issues(REPO, "tok-xyz", transport=httpx.MockTransport(handler))


def test_find_open_returns_the_issue_number():
    # id and number are both present and different, as on the real API: a
    # find_open that read "id" instead of "number" would return 987654321.
    handler, _ = recorder(
        [
            httpx.Response(
                200,
                json=[{"id": 987654321, "number": 42, "title": "Altro", "state": "open"}],
            )
        ]
    )
    with issues(handler) as api:
        assert api.find_open(LABEL) == 42


def test_find_open_sends_state_open_and_label_as_query_filters():
    handler, calls = recorder([httpx.Response(200, json=[])])
    with issues(handler) as api:
        api.find_open(LABEL)
    method, path, params, payload = calls[0]
    assert (method, path) == ("GET", f"/repos/{REPO}/issues")
    assert params == {"state": "open", "labels": LABEL}
    assert payload is None


def test_find_open_returns_none_when_no_issue_is_open():
    handler, _ = recorder([httpx.Response(200, json=[])])
    with issues(handler) as api:
        assert api.find_open(LABEL) is None


def test_create_posts_title_body_and_label():
    handler, calls = recorder(
        [
            httpx.Response(
                201,
                json={
                    "id": 555000111,
                    "number": 7,
                    "title": "Alternative disponibili",
                    "body": "Confronto aggiornato con il listino.",
                    "state": "open",
                },
            )
        ]
    )
    with issues(handler) as api:
        number = api.create(
            "Alternative disponibili", "Confronto aggiornato con il listino.", LABEL
        )
    assert number == 7
    method, path, _, payload = calls[0]
    assert (method, path) == ("POST", f"/repos/{REPO}/issues")
    # Exact structural equality, not substring checks: a swapped title/body
    # or a label landing in the wrong field would still contain the right
    # substrings but fail this.
    assert payload == {
        "title": "Alternative disponibili",
        "body": "Confronto aggiornato con il listino.",
        "labels": [LABEL],
    }


def test_create_sends_assignees_when_given():
    handler, calls = recorder([httpx.Response(201, json={"id": 555000111, "number": 8})])
    with issues(handler) as api:
        api.create("Titolo", "Corpo", LABEL, assignees=["owner-login"])
    assert calls[0][3] == {
        "title": "Titolo",
        "body": "Corpo",
        "labels": [LABEL],
        "assignees": ["owner-login"],
    }


def test_update_patches_only_the_body():
    handler, calls = recorder([httpx.Response(200, json={"number": 7, "state": "open"})])
    with issues(handler) as api:
        api.update(7, "Corpo aggiornato dopo il nuovo confronto.")
    method, path, _, payload = calls[0]
    assert (method, path) == ("PATCH", f"/repos/{REPO}/issues/7")
    # Exact equality (not "state" not in payload): also catches state being
    # sent with a value that would otherwise slip past a substring check.
    assert payload == {"body": "Corpo aggiornato dopo il nuovo confronto."}


def test_close_comments_then_closes():
    handler, calls = recorder(
        [
            httpx.Response(201, json={"id": 1, "body": "Chiudo, nessuna alternativa."}),
            httpx.Response(200, json={"number": 7, "state": "closed"}),
        ]
    )
    with issues(handler) as api:
        api.close(7, "Chiudo, nessuna alternativa.")
    assert [(method, path) for method, path, _, _ in calls] == [
        ("POST", f"/repos/{REPO}/issues/7/comments"),
        ("PATCH", f"/repos/{REPO}/issues/7"),
    ]
    # The comment must be posted (and, per the recorded order above, posted
    # first) before the state flips to closed, or the comment reads as
    # attached to an already-closed issue.
    assert calls[0][3] == {"body": "Chiudo, nessuna alternativa."}
    assert calls[1][3] == {"state": "closed"}


def test_http_errors_are_raised():
    handler, _ = recorder([httpx.Response(403, json={"message": "Forbidden"})])
    with issues(handler) as api:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            api.find_open(LABEL)
    assert excinfo.value.response.status_code == 403


def test_client_closes_even_if_the_with_block_raises():
    handler, _ = recorder([])
    api = issues(handler)
    with pytest.raises(RuntimeError, match="boom"):
        with api:
            raise RuntimeError("boom")
    # httpx.Client refuses any further request once closed; reaching that
    # guard (rather than hanging or reusing a live connection) is the only
    # black-box evidence that __exit__ ran despite the exception.
    with pytest.raises(RuntimeError, match="closed"):
        api.find_open(LABEL)
