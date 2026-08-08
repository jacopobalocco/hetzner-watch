import json

import pytest

from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline, Offer, Specs
from hetzner_watch.snapshot import Snapshot, build_snapshot, diff, has_changes, load, save

SPECS = Specs(2, 2.0, 40, "shared", "x86")
BASELINE = Baseline("srv", "CPX12", "fsn1", SPECS, 5.0, 20.0)


def candidate(
    name="CAX21",
    location="fsn1",
    price=7.0,
    cores=2,
    memory_gb=2.0,
    disk_gb=40,
    cpu_type="shared",
    architecture="x86",
    included_traffic_tib=20.0,
):
    specs = Specs(cores, memory_gb, disk_gb, cpu_type, architecture)
    offer = Offer(
        name, location, specs, price, included_traffic_tib, deprecated=False, available=True
    )
    return Candidate(offer, (Delta("memory_gb", 2.0, 8.0, True),), needs_multiarch=False)


def snapshot(*candidates, generated_at="2026-08-07T07:00:00Z"):
    return build_snapshot(BASELINE, list(candidates), generated_at)


def test_snapshot_keys_are_type_at_location():
    assert set(snapshot(candidate()).candidates) == {"CAX21@fsn1"}


def test_snapshot_key_location_reflects_the_offer_not_a_default():
    # Every other test in this file uses candidate()'s default location
    # ("fsn1"), which coincides with BASELINE's own location. A key builder
    # that hardcoded "fsn1" instead of reading the offer's location would
    # pass all of them; this uses a distinct location to catch that.
    assert set(snapshot(candidate(location="nbg1")).candidates) == {"CAX21@nbg1"}


def test_snapshot_records_traffic_under_the_tib_key():
    # Hetzner reports traffic in bytes base 1024 (tebibytes); the key name
    # must say so, and it must actually come from included_traffic_tib.
    entry = snapshot(candidate()).candidates["CAX21@fsn1"]
    assert entry["included_traffic_tib"] == 20.0
    assert "included_traffic_tb" not in entry


def test_diff_against_no_previous_marks_everything_new():
    changes = diff(None, snapshot(candidate()))
    assert changes.new == ("CAX21@fsn1",)
    assert changes.price_changed == ()
    assert changes.gone == ()


def test_diff_detects_new_entries():
    before = snapshot(candidate("CAX21"))
    after = snapshot(candidate("CAX21"), candidate("CCX13", price=13.0))
    assert diff(before, after).new == ("CCX13@fsn1",)


def test_diff_detects_price_changes():
    before = snapshot(candidate(price=7.0))
    after = snapshot(candidate(price=6.5))
    assert diff(before, after).price_changed == (("CAX21@fsn1", 7.0, 6.5),)


def test_diff_detects_disappearances():
    before = snapshot(candidate("CAX21"), candidate("CCX13", price=13.0))
    after = snapshot(candidate("CAX21"))
    assert diff(before, after).gone == ("CCX13@fsn1",)


def test_identical_snapshots_have_no_changes():
    assert has_changes(diff(snapshot(candidate()), snapshot(candidate()))) is False


def test_has_changes_is_true_when_there_are_changes():
    # Without this, a stub that always returns False would still pass the
    # "no changes" tests above.
    changes = diff(None, snapshot(candidate()))
    assert has_changes(changes) is True


def test_a_different_timestamp_alone_is_not_a_change():
    before = snapshot(candidate(), generated_at="2026-08-06T07:00:00Z")
    after = snapshot(candidate(), generated_at="2026-08-07T07:00:00Z")
    assert has_changes(diff(before, after)) is False


def test_diff_detects_spec_changes_at_an_unchanged_price():
    # Finding 3: if disk changes 40 -> 80 at the same price, diff must not
    # stay empty - an empty diff there is an absorbing state (has_changes
    # False forever, the snapshot can never self-heal).
    before = snapshot(candidate(disk_gb=40))
    after = snapshot(candidate(disk_gb=80))
    changes = diff(before, after)
    assert changes.spec_changed == ("CAX21@fsn1",)
    assert changes.price_changed == ()


def test_spec_change_alone_counts_as_a_change():
    before = snapshot(candidate(disk_gb=40))
    after = snapshot(candidate(disk_gb=80))
    assert has_changes(diff(before, after)) is True


def test_unchanged_candidates_report_no_spec_change():
    before = snapshot(candidate(disk_gb=40))
    after = snapshot(candidate(disk_gb=40))
    assert diff(before, after).spec_changed == ()


@pytest.mark.parametrize(
    "field, before_value, after_value",
    [
        ("cores", 2, 4),
        ("memory_gb", 2.0, 4.0),
        ("cpu_type", "shared", "dedicated"),
        ("architecture", "x86", "arm"),
        ("included_traffic_tib", 20.0, 21.0),
    ],
)
def test_diff_detects_spec_changes_on_every_non_price_field(field, before_value, after_value):
    # Finding 3: the existing spec-change tests above only ever vary disk_gb,
    # so a _non_price narrowed to {"disk_gb": ...} would still pass all of
    # them. Cover the remaining five fields individually.
    before = snapshot(candidate(**{field: before_value}))
    after = snapshot(candidate(**{field: after_value}))
    changes = diff(before, after)
    assert changes.spec_changed == ("CAX21@fsn1",)
    assert changes.price_changed == ()


def test_diff_detects_baseline_changes():
    # Finding 4: Snapshot.baseline is recorded but never compared - resizing
    # cpx12 -> cx33 with an unchanged candidate set must still be a change,
    # or the issue keeps showing the old baseline and old deltas forever.
    before = snapshot(candidate())
    resized = Baseline("srv", "CX33", "fsn1", SPECS, 8.49, 20.0)
    after = build_snapshot(resized, [candidate()], "2026-08-07T07:00:00Z")
    assert diff(before, after).baseline_changed is True


def test_diff_detects_a_baseline_change_on_a_field_other_than_type_name():
    # Finding 4: the test above changes type_name and price together, so a
    # baseline_changed narrowed to comparing only type_name would still pass
    # it. Change price alone, with type_name held fixed, to pin that down.
    before = snapshot(candidate())
    repriced = Baseline("srv", "CPX12", "fsn1", SPECS, 5.49, 20.0)
    after = build_snapshot(repriced, [candidate()], "2026-08-07T07:00:00Z")
    assert diff(before, after).baseline_changed is True


def test_baseline_change_alone_counts_as_a_change():
    before = snapshot(candidate())
    resized = Baseline("srv", "CX33", "fsn1", SPECS, 8.49, 20.0)
    after = build_snapshot(resized, [candidate()], "2026-08-07T07:00:00Z")
    assert has_changes(diff(before, after)) is True


def test_unchanged_baseline_is_not_flagged():
    before = snapshot(candidate())
    after = snapshot(candidate())
    assert diff(before, after).baseline_changed is False


def test_first_run_does_not_flag_baseline_changed():
    # previous is None on day one: there is no prior baseline to compare
    # against, and "new" already covers everything in that case.
    changes = diff(None, snapshot(candidate()))
    assert changes.baseline_changed is False


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "snapshot.json"
    original = snapshot(candidate())
    save(path, original)

    # Assert on the actual file content, not just the object diff() returns,
    # so a save() that silently no-ops can't be masked by a load() that
    # hands back the in-memory original instead of reading the file.
    on_disk = json.loads(path.read_text())
    assert on_disk["candidates"]["CAX21@fsn1"]["price_eur_net"] == 7.0

    loaded = load(path)
    assert loaded == original
    assert loaded is not original


def test_load_returns_none_when_absent(tmp_path):
    assert load(tmp_path / "missing.json") is None
