import json
import pathlib

import pytest

import hetzner_watch.availability as availability_module
from hetzner_watch.availability import (
    CLOSE,
    NOOP,
    OPEN,
    decide,
    label,
    location_availability,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def cx43(**available_by_location):
    return {
        "server_types": [
            {"name": "cx33", "locations": [{"name": "fsn1", "available": True}]},
            {
                "name": "cx43",
                "locations": [
                    {"name": name, "available": flag}
                    for name, flag in available_by_location.items()
                ],
            },
        ]
    }


def test_availability_is_read_per_location_from_the_watched_type_only():
    # cx33 is available in fsn1: reading the wrong type would answer True.
    catalog = cx43(fsn1=False, nbg1=True)
    assert location_availability(catalog, "cx43", ("fsn1", "nbg1")) == {
        "fsn1": False,
        "nbg1": True,
    }


def test_a_location_the_type_does_not_list_reads_as_unavailable():
    assert location_availability(cx43(fsn1=True), "cx43", ("hel1",)) == {"hel1": False}


def test_a_type_missing_from_the_catalog_is_an_error_not_a_sold_out():
    with pytest.raises(ValueError, match="cx99"):
        location_availability(cx43(fsn1=True), "cx99", ("fsn1",))


def test_the_real_fixture_reads_cx43_available_in_fsn1():
    catalog = json.loads((FIXTURES / "server_types.json").read_text())
    assert location_availability(catalog, "cx43", ("fsn1",)) == {"fsn1": True}


@pytest.mark.parametrize(
    ("available", "open_issue", "expected"),
    [
        (True, None, OPEN),  # sold out -> available: the one alert
        (True, 7, NOOP),  # still available: no repeat
        (False, 7, CLOSE),  # available -> sold out: re-arm
        (False, None, NOOP),  # still sold out
    ],
)
def test_decide_alerts_only_on_the_transition_to_available(available, open_issue, expected):
    assert decide(available, open_issue) == expected


def test_each_location_has_its_own_label():
    assert label("cx43", "fsn1") != label("cx43", "nbg1")


def fake_issues(open_by_label):
    calls = []

    class FakeIssues:
        def __init__(self, repo, token, transport=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def find_open(self, issue_label):
            return open_by_label.get(issue_label)

        def create(self, title, body, issue_label, assignees=None):
            calls.append(("create", issue_label, assignees))
            return 99

        def close(self, number, comment):
            calls.append(("close", number))

    return FakeIssues, calls


def test_main_opens_closes_and_leaves_alone_per_location(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("locations: [fsn1, nbg1, hel1]\nwatch_type: cx43\n")
    monkeypatch.setattr(availability_module, "CONFIG_PATH", config)
    monkeypatch.setenv("HCLOUD_TOKEN", "tok-a,tok-b")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/infra-fleet")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "me")
    tokens = []

    def fetch_server_types(token, transport=None):
        tokens.append(token)
        return cx43(fsn1=True, nbg1=False, hel1=True)

    monkeypatch.setattr(availability_module.hetzner, "fetch_server_types", fetch_server_types)
    issues_cls, calls = fake_issues(
        {label("cx43", "nbg1"): 4, label("cx43", "hel1"): 5}
    )
    monkeypatch.setattr(availability_module, "Issues", issues_cls)

    availability_module.main()

    assert tokens == ["tok-a"]
    assert calls == [
        ("create", label("cx43", "fsn1"), ["me"]),
        ("close", 4),
    ]


def test_main_refuses_an_empty_token(monkeypatch, tmp_path):
    # The 2026-08/10 failures: the secret was never set, the env var is "".
    config = tmp_path / "config.yaml"
    config.write_text("locations: [fsn1]\nwatch_type: cx43\n")
    monkeypatch.setattr(availability_module, "CONFIG_PATH", config)
    monkeypatch.setenv("HCLOUD_TOKEN", "")
    with pytest.raises(SystemExit, match="HCLOUD_TOKEN"):
        availability_module.main()
