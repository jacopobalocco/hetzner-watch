import pathlib

import pytest

import hetzner_watch.__main__ as main_module
from hetzner_watch.__main__ import decide, load_config, should_save
from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline, Offer, Specs, parse_baselines
from hetzner_watch.report import CLOSE_COMMENT_TEXT, ISSUE_TITLE
from hetzner_watch.snapshot import Changes, build_snapshot, diff, load, save

SPECS = Specs(2, 2.0, 40, "shared", "x86")
BASELINE = Baseline("srv", "CPX12", "fsn1", SPECS, 5.0, 20.0)
NO_CHANGES = Changes(new=(), price_changed=(), gone=())
SOME_CHANGES = Changes(new=("CAX21@fsn1",), price_changed=(), gone=())
CANDIDATE = Candidate(
    Offer("CAX21", "fsn1", SPECS, 7.0, 20.0, deprecated=False, available=True),
    (Delta("memory_gb", 2.0, 8.0, True),),
    needs_multiarch=False,
)


CONFIG_PATH = pathlib.Path(__file__).parent.parent / "config.yaml"


def test_config_is_read_from_yaml():
    config = load_config(CONFIG_PATH)
    assert config.max_price_eur_net == 20.0
    assert config.locations == ("fsn1", "nbg1", "hel1")
    assert config.include_arm is True


def test_candidates_without_an_open_issue_create_one():
    action = decide(None, [CANDIDATE], BASELINE, SOME_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "create"
    assert "CAX21" in action.body


def test_candidates_without_an_open_issue_create_one_even_without_changes():
    # "create" must depend only on open_issue being None, not on has_changes:
    # the table's row for candidates=yes/issue=no marks the changes column "-".
    action = decide(None, [CANDIDATE], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "create"
    assert "CAX21" in action.body


def test_candidates_with_changes_update_the_open_issue():
    action = decide(7, [CANDIDATE], BASELINE, SOME_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "update"
    assert "CAX21" in action.body


def test_candidates_without_changes_do_nothing():
    # Same open_issue and candidates as the update test above; only the
    # changes flag differs, so this pins the update/noop split precisely
    # rather than something coincidental about the other inputs.
    action = decide(7, [CANDIDATE], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "noop"


def test_no_candidates_closes_the_open_issue():
    action = decide(7, [], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "close"
    assert "2026-08-07T07:00:00Z" in action.body


def test_no_candidates_closes_the_open_issue_even_with_changes():
    # "close" must depend only on open_issue being set, not on has_changes:
    # the table's row for candidates=no/issue=yes marks the changes column "-".
    action = decide(7, [], BASELINE, SOME_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "close"
    assert "2026-08-07T07:00:00Z" in action.body


def test_no_candidates_and_no_issue_does_nothing():
    action = decide(None, [], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z", True)
    assert action.kind == "noop"
    # Empty, distinctly not the close comment: a table that fell through to
    # the close branch regardless of open_issue would still report "noop"
    # in a naive kind-only check but would leak the close body here.
    assert action.body == ""


# --- save-only-when-changed ruling ---
#
# generated_at is a fresh timestamp every run, so saving snapshot.json
# unconditionally would dirty the file daily even when nothing changed,
# turning the commit history (meant to read as a price time series) into
# noise. should_save must return True only on the first run (no previous
# snapshot) or when there is something to record.


def test_should_save_is_true_on_the_first_run_even_without_changes():
    assert should_save(None, NO_CHANGES) is True


def test_should_save_is_true_when_there_are_changes():
    previous = build_snapshot(BASELINE, [], "2026-08-06T07:00:00Z")
    assert should_save(previous, SOME_CHANGES) is True


def test_should_save_is_false_without_changes_and_a_previous_snapshot():
    previous = build_snapshot(BASELINE, [CANDIDATE], "2026-08-06T07:00:00Z")
    assert should_save(previous, NO_CHANGES) is False


def test_no_changes_leaves_the_committed_snapshot_file_byte_identical(tmp_path):
    path = tmp_path / "snapshot.json"
    previous = build_snapshot(BASELINE, [CANDIDATE], "2026-08-06T07:00:00Z")
    save(path, previous)
    before = path.read_bytes()

    # Same candidates, only generated_at moved forward: this is exactly the
    # "another day, nothing changed" run the ruling exists for.
    current = build_snapshot(BASELINE, [CANDIDATE], "2026-08-07T07:00:00Z")
    changes = diff(previous, current)

    if should_save(previous, changes):
        save(path, current)

    assert path.read_bytes() == before


def test_changes_do_update_the_committed_snapshot_file(tmp_path):
    path = tmp_path / "snapshot.json"
    previous = build_snapshot(BASELINE, [CANDIDATE], "2026-08-06T07:00:00Z")
    save(path, previous)

    current = build_snapshot(BASELINE, [], "2026-08-07T07:00:00Z")
    changes = diff(previous, current)

    if should_save(previous, changes):
        save(path, current)

    assert load(path) == current


# --- main() wiring: multi-token handling, the single-catalog-fetch guard,
# the "no servers anywhere" hard failure, and dispatch to the Issues client.
#
# hetzner.fetch and Issues are the only two collaborators main() doesn't
# control the shape of (real network). Both already support test-time
# substitution elsewhere in this suite (transport=None injection in
# test_hetzner.py / test_github.py); here they're monkeypatched at the
# names main() actually calls through: hetzner_watch.__main__.hetzner.fetch
# and hetzner_watch.__main__.Issues.


def server_type(id_, name, location, price, memory=2, traffic_bytes=20 * 1024**4):
    return {
        "id": id_,
        "name": name,
        "cores": 1,
        "memory": memory,
        "disk": 40,
        "cpu_type": "shared",
        "architecture": "x86",
        "prices": [
            {
                "location": location,
                "price_monthly": {"net": str(price)},
                "included_traffic": traffic_bytes,
            }
        ],
        # available lives here, not in the datacenters payload (Finding 1 /
        # docs/api-findings.md #6): parse_offers reads locations[].available.
        "locations": [{"name": location, "available": True, "deprecation": None}],
    }


def server(name, type_name, location="fsn1"):
    return {
        "name": name,
        "location": {"name": location},
        "server_type": {
            "name": type_name,
            "cores": 1,
            "memory": 2,
            "disk": 40,
            "cpu_type": "shared",
            "architecture": "x86",
        },
    }


def datacenter(location, available_ids):
    # Content is inert since Finding 1 (hetzner.fetch still returns a
    # 3-tuple, so fake_fetch's stand-ins must match its shape, but
    # parse_offers no longer reads this payload for availability).
    return {"location": {"name": location}, "server_types": {"available": available_ids}}


# CPX12 (id 1) is the baseline's own type: find_candidates excludes it as its
# own baseline, so its presence in the catalog is inert except as bait for a
# broken exclusion. CAX21 (id 2) is a genuine candidate (more memory, under
# the price cap).
CPX12_TYPE = server_type(1, "CPX12", "fsn1", 5.0, memory=2)
CAX21_TYPE = server_type(2, "CAX21", "fsn1", 7.0, memory=8)
CCX13_TYPE = server_type(3, "CCX13", "fsn1", 6.0, memory=8)

SERVER_TYPES_JSON = {"server_types": [CPX12_TYPE, CAX21_TYPE]}
DATACENTERS_JSON = {"datacenters": [datacenter("fsn1", [1, 2])]}
SERVERS_JSON = {"servers": [server("srv-a", "CPX12", "fsn1")]}
EMPTY_SERVERS_JSON = {"servers": []}

# A second, distinguishable catalog: shares the baseline's own type (CPX12)
# but offers CCX13 instead of CAX21. Used to prove the "if not offers" guard
# keeps the first token's catalog rather than the last one's.
SERVER_TYPES_JSON_B = {"server_types": [CPX12_TYPE, CCX13_TYPE]}
DATACENTERS_JSON_B = {"datacenters": [datacenter("fsn1", [1, 3])]}

# A catalog with no viable alternative at all (only the baseline's own
# type), for the close-the-issue scenario.
SERVER_TYPES_JSON_NO_CANDIDATES = {"server_types": [CPX12_TYPE]}
DATACENTERS_JSON_NO_CANDIDATES = {"datacenters": [datacenter("fsn1", [1])]}


def fake_fetch(by_token):
    calls = []

    def fetch(token, transport=None):
        calls.append(token)
        return by_token[token]

    return fetch, calls


def fake_issues(open_issue):
    # Records only the mutating calls (create/update/close): find_open is a
    # lookup, not an action, and isn't part of what these tests pin.
    calls = []

    class FakeIssues:
        def __init__(self, repo, token, transport=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def find_open(self, label):
            return open_issue

        def create(self, title, body, label):
            calls.append(("create", title, body, label))
            return 99

        def update(self, number, body):
            calls.append(("update", number, body))

        def close(self, number, comment):
            calls.append(("close", number, comment))

    return FakeIssues, calls


def set_env(monkeypatch, tmp_path, hcloud_token):
    monkeypatch.setenv("HCLOUD_TOKEN", hcloud_token)
    monkeypatch.setenv("GITHUB_TOKEN", "gh-tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/infra-fleet")
    monkeypatch.setattr(main_module, "SNAPSHOT_PATH", tmp_path / "snapshot.json")


def test_multiple_comma_separated_tokens_are_all_used_and_baselines_accumulate(
    monkeypatch, tmp_path
):
    set_env(monkeypatch, tmp_path, " tok-a , tok-b ")
    fetch, fetch_calls = fake_fetch(
        {
            "tok-a": (SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON),
            "tok-b": (EMPTY_SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON),
        }
    )
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=None)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    main_module.main()

    # Both comma-separated tokens (whitespace stripped) were fetched, not
    # just the first: a broken split, or a loop reading only tokens[0],
    # would leave "tok-b" uncalled.
    assert fetch_calls == ["tok-a", "tok-b"]
    # tok-a's server survives as the baseline used. If baselines were
    # assigned with `=` instead of accumulated with `+=`, tok-b's *empty*
    # result would have overwritten tok-a's non-empty one and this run
    # would have hit "nessun server trovato" instead of creating an issue.
    assert len(issue_calls) == 1
    kind, title, body, label = issue_calls[0]
    assert kind == "create"
    assert title == ISSUE_TITLE
    assert label == main_module.LABEL
    assert "CAX21" in body


def test_warns_on_stdout_when_more_than_one_server_is_found(monkeypatch, tmp_path, capsys):
    # Finding 6: dropping every baseline but the first must be visible, not a
    # silent narrowing - the warning must name the count and which server won.
    set_env(monkeypatch, tmp_path, "tok-a")
    second_server = server("srv-b", "CPX12", "fsn1")
    servers_with_two = {"servers": [*SERVERS_JSON["servers"], second_server]}
    fetch, _ = fake_fetch({"tok-a": (servers_with_two, SERVER_TYPES_JSON, DATACENTERS_JSON)})
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, _ = fake_issues(open_issue=None)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    main_module.main()

    out = capsys.readouterr().out
    assert "2 server" in out
    assert "srv-a" in out
    assert "srv-b" in out


def test_does_not_warn_when_exactly_one_server_is_found(monkeypatch, tmp_path, capsys):
    set_env(monkeypatch, tmp_path, "tok-a")
    fetch, _ = fake_fetch({"tok-a": (SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON)})
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, _ = fake_issues(open_issue=None)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    main_module.main()

    assert "trovati" not in capsys.readouterr().out


def test_catalog_is_fetched_from_the_first_token_only(monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, "tok-a,tok-b")
    fetch, fetch_calls = fake_fetch(
        {
            "tok-a": (SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON),
            "tok-b": (EMPTY_SERVERS_JSON, SERVER_TYPES_JSON_B, DATACENTERS_JSON_B),
        }
    )
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=None)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    main_module.main()

    assert fetch_calls == ["tok-a", "tok-b"]
    body = issue_calls[0][2]
    # tok-a's catalog (CAX21) must win. If the "if not offers" guard were
    # dropped, tok-b's catalog would silently overwrite it (offers is
    # reassigned, not merged), and CCX13 would show up instead.
    assert "CAX21" in body
    assert "CCX13" not in body


def test_no_servers_across_any_token_exits_with_a_clear_error(monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, "tok-a,tok-b")
    fetch, _ = fake_fetch(
        {
            "tok-a": (EMPTY_SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON),
            "tok-b": (EMPTY_SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON),
        }
    )
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=None)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    with pytest.raises(SystemExit, match="nessun server trovato"):
        main_module.main()

    # A silent "0 candidates" report would still reach the Issues API; the
    # hard error must short-circuit before that.
    assert issue_calls == []


def test_main_creates_the_issue_when_none_is_open(monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, "tok-a")
    fetch, _ = fake_fetch({"tok-a": (SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON)})
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=None)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    main_module.main()

    assert len(issue_calls) == 1
    kind, title, body, label = issue_calls[0]
    assert (kind, title, label) == ("create", ISSUE_TITLE, main_module.LABEL)
    assert "CAX21" in body


def test_main_updates_the_issue_when_the_candidate_set_changed(monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, "tok-a")
    fetch, _ = fake_fetch({"tok-a": (SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON)})
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=7)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    previous = build_snapshot(BASELINE, [], "2026-08-06T07:00:00Z")
    save(main_module.SNAPSHOT_PATH, previous)

    main_module.main()

    assert len(issue_calls) == 1
    kind, number, body = issue_calls[0]
    assert (kind, number) == ("update", 7)
    assert "CAX21" in body


def test_main_closes_the_issue_when_no_candidates_remain(monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, "tok-a")
    fetch, _ = fake_fetch(
        {
            "tok-a": (
                SERVERS_JSON,
                SERVER_TYPES_JSON_NO_CANDIDATES,
                DATACENTERS_JSON_NO_CANDIDATES,
            )
        }
    )
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=7)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    main_module.main()

    assert len(issue_calls) == 1
    kind, number, comment = issue_calls[0]
    assert (kind, number) == ("close", 7)
    assert CLOSE_COMMENT_TEXT in comment


def test_main_does_nothing_on_the_issues_client_when_nothing_changed(monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, "tok-a")
    fetch, _ = fake_fetch({"tok-a": (SERVERS_JSON, SERVER_TYPES_JSON, DATACENTERS_JSON)})
    monkeypatch.setattr(main_module.hetzner, "fetch", fetch)
    issues_cls, issue_calls = fake_issues(open_issue=7)
    monkeypatch.setattr(main_module, "Issues", issues_cls)

    # Matches exactly what the real pipeline computes for CAX21 above, so
    # the diff sees no change at all (not even a price move).
    matching_offer = Offer(
        "CAX21", "fsn1", Specs(1, 8.0, 40, "shared", "x86"), 7.0, 20.0,
        deprecated=False, available=True,
    )
    matching_candidate = Candidate(
        matching_offer, (Delta("memory_gb", 2.0, 8.0, True),), needs_multiarch=False
    )
    # Finding 4 makes diff() compare the baseline too, so - like the
    # candidate above - it must match exactly what the real pipeline
    # computes from SERVERS_JSON/SERVER_TYPES_JSON, not the module-level
    # BASELINE stand-in (which has no relation to those fixtures' values).
    real_baseline = parse_baselines(SERVERS_JSON, SERVER_TYPES_JSON)[0]
    previous = build_snapshot(real_baseline, [matching_candidate], "2026-08-06T07:00:00Z")
    save(main_module.SNAPSHOT_PATH, previous)

    main_module.main()

    assert issue_calls == []
