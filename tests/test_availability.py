import datetime as dt
import json
import pathlib

import pytest

import hetzner_watch.availability as availability_module
from hetzner_watch.availability import (
    load_state,
    location_availability,
    newly_available,
    save_state,
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
    ("previous", "current", "expected"),
    [
        ({"fsn1": False}, {"fsn1": True}, ["fsn1"]),  # sold out -> available: the alert
        ({"fsn1": True}, {"fsn1": True}, []),  # still available: no repeat
        ({"fsn1": True}, {"fsn1": False}, []),  # sold out again: silent, re-armed
        ({"fsn1": False}, {"fsn1": False}, []),
        ({}, {"fsn1": True}, ["fsn1"]),  # first run, already available
        ({"fsn1": True}, {"fsn1": True, "nbg1": True}, ["nbg1"]),
    ],
)
def test_only_a_turn_to_available_is_news(previous, current, expected):
    assert newly_available(previous, current) == expected


def test_state_round_trips_and_starts_empty(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path) == {}
    save_state(path, {"fsn1": True, "nbg1": False})
    assert load_state(path) == {"fsn1": True, "nbg1": False}


def run_main(monkeypatch, tmp_path, catalog, sent, fail_send=False):
    config = tmp_path / "config.yaml"
    config.write_text("locations: [fsn1, nbg1]\nwatch_type: cx43\nvm_location: fsn1\n")
    monkeypatch.setattr(availability_module, "CONFIG_PATH", config)
    monkeypatch.setattr(availability_module, "on_this_host", lambda raw: True)
    monkeypatch.setattr(availability_module, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("HCLOUD_TOKEN", "tok-a,tok-b")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pw")
    monkeypatch.setattr(
        availability_module.hetzner, "fetch_server_types", lambda token: catalog
    )
    monkeypatch.setattr(availability_module, "render_mail", lambda *a: ("s", "t", "h"))

    def send_mail(subject, text, html, app_password):
        if fail_send:
            raise OSError("smtp down")
        sent.append(app_password)

    monkeypatch.setattr(availability_module, "send_mail", send_mail)
    availability_module.main()


def test_main_mails_once_across_hours_and_again_after_a_sell_out(monkeypatch, tmp_path):
    sent = []
    run_main(monkeypatch, tmp_path, cx43(fsn1=False, nbg1=False), sent)
    run_main(monkeypatch, tmp_path, cx43(fsn1=True, nbg1=False), sent)
    run_main(monkeypatch, tmp_path, cx43(fsn1=True, nbg1=False), sent)
    assert len(sent) == 1
    run_main(monkeypatch, tmp_path, cx43(fsn1=False, nbg1=False), sent)
    run_main(monkeypatch, tmp_path, cx43(fsn1=True, nbg1=False), sent)
    assert len(sent) == 2


def test_a_failed_mail_leaves_the_state_so_the_next_run_retries(monkeypatch, tmp_path):
    sent = []
    with pytest.raises(OSError):
        run_main(monkeypatch, tmp_path, cx43(fsn1=True, nbg1=False), sent, fail_send=True)
    run_main(monkeypatch, tmp_path, cx43(fsn1=True, nbg1=False), sent)
    assert len(sent) == 1


def test_main_refuses_missing_secrets(monkeypatch, tmp_path):
    # The GitHub job's failures of 2026-08/10: a token that was never set.
    monkeypatch.setenv("HCLOUD_TOKEN", "")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(availability_module, "on_this_host", lambda raw: True)
    with pytest.raises(SystemExit, match="HCLOUD_TOKEN"):
        availability_module.main()


def test_the_mail_says_whether_the_vms_location_is_among_them():
    pytest.importorskip("mailstile")
    when = dt.datetime(2026, 10, 14, 17, 17, tzinfo=dt.UTC)
    subject, text, html = availability_module.render_mail("cx43", ["fsn1"], "fsn1", when)
    assert "cx43" in subject and "fsn1" in subject
    assert "dove sta la VM: il ridimensionamento è possibile" in text
    _, text, _ = availability_module.render_mail("cx43", ["nbg1"], "fsn1", when)
    assert "Non in fsn1" in text
    assert "<style" not in html


def test_another_host_neither_reads_nor_mails(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("watch_type: cx43\nvm_location: fsn1\nlocations: [fsn1]\nrun_on_host: Elsewhere\n")
    monkeypatch.setattr(availability_module, "CONFIG_PATH", config)
    monkeypatch.setattr(availability_module.hetzner, "fetch_server_types", lambda *a: pytest.fail("read on the wrong host"))
    availability_module.main()
