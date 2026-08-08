import json
import pathlib

import pytest

from hetzner_watch.__main__ import load_config
from hetzner_watch.compare import Config, find_candidates, location_has_availability
from hetzner_watch.hetzner import Baseline, Offer, Specs, parse_baselines, parse_offers
from hetzner_watch.snapshot import key as snapshot_key

CONFIG = Config(max_price_eur_net=20.0, locations=("fsn1", "nbg1", "hel1"), include_arm=True)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CONFIG_PATH = pathlib.Path(__file__).parent.parent / "config.yaml"


def specs(cores=2, memory_gb=2.0, disk_gb=40, cpu_type="shared", architecture="x86"):
    return Specs(cores, memory_gb, disk_gb, cpu_type, architecture)


@pytest.fixture
def baseline():
    return Baseline(
        server_name="example-server-1",
        type_name="CPX12",
        location="fsn1",
        specs=specs(),
        price_eur_net=5.0,
        included_traffic_tib=20.0,
    )


def offer(name="CX99", location="fsn1", price=6.0, traffic=20.0, available=True,
          deprecated=False, **spec_kwargs):
    return Offer(
        type_name=name,
        location=location,
        specs=specs(**spec_kwargs),
        price_eur_net=price,
        included_traffic_tib=traffic,
        deprecated=deprecated,
        available=available,
    )


def names(candidates):
    return [c.offer.type_name for c in candidates]


def test_price_cap_is_inclusive_at_the_threshold(baseline):
    offers = [
        offer(name="AT_CAP", price=20.0, memory_gb=8.0),
        offer(name="OVER_CAP", price=20.01, memory_gb=8.0),
    ]
    assert names(find_candidates(baseline, offers, CONFIG)) == ["AT_CAP"]


def test_one_improvement_wins_despite_regressions(baseline):
    offers = [offer(name="TRADEOFF", price=7.0, memory_gb=8.0, cores=1, disk_gb=20, traffic=1.0)]
    candidates = find_candidates(baseline, offers, CONFIG)
    assert names(candidates) == ["TRADEOFF"]
    assert {d.metric for d in candidates[0].deltas if not d.better} == {
        "cores", "disk_gb", "included_traffic_tib", "price_eur_net"
    }


def test_no_improvement_is_excluded(baseline):
    offers = [offer(name="STRICTLY_WORSE", price=9.0, memory_gb=1.0, cores=1, disk_gb=20)]
    assert find_candidates(baseline, offers, CONFIG) == []


def test_identical_offer_is_excluded(baseline):
    offers = [offer(name="CPX12", price=5.0)]
    assert find_candidates(baseline, offers, CONFIG) == []


def test_cheaper_with_identical_specs_is_a_candidate(baseline):
    offers = [offer(name="CHEAPER", price=4.0)]
    candidates = find_candidates(baseline, offers, CONFIG)
    assert names(candidates) == ["CHEAPER"]
    assert [(d.metric, d.better) for d in candidates[0].deltas] == [("price_eur_net", True)]


def test_deltas_only_cover_metrics_that_differ(baseline):
    offers = [offer(name="MORE_RAM", price=5.0, memory_gb=4.0)]
    deltas = find_candidates(baseline, offers, CONFIG)[0].deltas
    assert [(d.metric, d.baseline, d.candidate, d.better) for d in deltas] == [
        ("memory_gb", 2.0, 4.0, True)
    ]


def test_unavailable_type_is_excluded(baseline):
    offers = [offer(name="SOLD_OUT", price=6.0, memory_gb=8.0, available=False)]
    assert find_candidates(baseline, offers, CONFIG) == []


def test_deprecated_type_is_excluded(baseline):
    offers = [offer(name="OLD", price=6.0, memory_gb=8.0, deprecated=True)]
    assert find_candidates(baseline, offers, CONFIG) == []


def test_non_eu_location_is_excluded(baseline):
    offers = [offer(name="ASHBURN", location="ash", price=6.0, memory_gb=8.0)]
    assert find_candidates(baseline, offers, CONFIG) == []


def test_arm_is_included_and_flagged(baseline):
    offers = [offer(name="CAX21", price=7.0, memory_gb=8.0, architecture="arm")]
    candidates = find_candidates(baseline, offers, CONFIG)
    assert names(candidates) == ["CAX21"]
    assert candidates[0].needs_multiarch is True


def test_arm_can_be_switched_off(baseline):
    offers = [offer(name="CAX21", price=7.0, memory_gb=8.0, architecture="arm")]
    config = Config(max_price_eur_net=20.0, locations=("fsn1",), include_arm=False)
    assert find_candidates(baseline, offers, config) == []


def test_unknown_cpu_type_raises_a_clear_error(baseline):
    # Finding 8: an unexplained KeyError on CPU_RANK[...] gives no clue what
    # broke; hard-failing is right, but the message must say what happened.
    offers = [offer(name="MYSTERY", price=6.0, cpu_type="quantum")]
    with pytest.raises(ValueError, match="quantum"):
        find_candidates(baseline, offers, CONFIG)


def test_dedicated_cpu_counts_as_an_improvement(baseline):
    offers = [offer(name="CCX13", price=13.0, cpu_type="dedicated")]
    candidates = find_candidates(baseline, offers, CONFIG)
    assert names(candidates) == ["CCX13"]
    assert ("cpu_type", True) in [(d.metric, d.better) for d in candidates[0].deltas]


def test_location_has_availability_is_true_when_any_offer_there_is_available():
    offers = [offer(name="CX23", location="fsn1", available=True)]
    assert location_has_availability("fsn1", offers) is True


def test_location_has_availability_is_false_when_none_there_are_available():
    # Finding 2: this is the scenario a stale availability signal produces -
    # every offer at the baseline's own location reads unavailable, and the
    # report must say so instead of silently showing only other locations.
    offers = [
        offer(name="CX23", location="fsn1", available=False),
        offer(name="CX23", location="nbg1", available=True),
    ]
    assert location_has_availability("fsn1", offers) is False


def test_location_has_availability_ignores_other_locations():
    offers = [offer(name="CX23", location="nbg1", available=True)]
    assert location_has_availability("fsn1", offers) is False


def test_candidates_are_sorted_by_price_then_name(baseline):
    # Price and alphabetical order deliberately conflict (cheapest name sorts
    # last, priciest name sorts first) so only the correct key order passes.
    # The tied pair is listed out of alphabetical order too, so a tie-break
    # drop is not masked by input order coinciding with the expected order.
    offers = [
        offer(name="M_TIED", price=9.0, memory_gb=4.0),
        offer(name="Z_CHEAP", price=6.0, memory_gb=4.0),
        offer(name="A_TIED", price=9.0, memory_gb=4.0),
    ]
    assert names(find_candidates(baseline, offers, CONFIG)) == ["Z_CHEAP", "A_TIED", "M_TIED"]


# --- end-to-end against the real captured fixtures (Finding 5) ---
#
# Every test above builds synthetic offers; that blindness let Finding 1's
# datacenters-availability bug survive eight review rounds. This runs the
# actual parse -> compare pipeline against tests/fixtures/*.json and the
# real config.yaml, and pins the exact answer the first real run reports.


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# type@location keys the real fixtures + config.yaml produce via
# parse_baselines -> parse_offers -> find_candidates, once Finding 1's
# per-location `available` signal is used. cx23/cx33/cx43 are the fsn1
# in-place resize candidates the console check confirmed are orderable now;
# cpx22 clears the 20 EUR cap at 19.49. cx43@hel1 and every cax* (ARM) type
# are absent because their real per-location `available` reads false.
EXPECTED_REAL_CANDIDATE_KEYS = {
    "cx23@fsn1", "cx23@hel1", "cx23@nbg1",
    "cx33@fsn1", "cx33@hel1", "cx33@nbg1",
    "cx43@fsn1", "cx43@nbg1",
    "cpx22@fsn1", "cpx22@hel1", "cpx22@nbg1",
}


def test_real_fixtures_produce_the_documented_candidate_set():
    servers_json = _load_fixture("servers")
    server_types_json = _load_fixture("server_types")
    config = load_config(CONFIG_PATH)

    baseline = parse_baselines(servers_json, server_types_json)[0]
    offers = parse_offers(server_types_json)
    candidates = find_candidates(baseline, offers, config)

    keys = {snapshot_key(c.offer.type_name, c.offer.location) for c in candidates}
    assert keys == EXPECTED_REAL_CANDIDATE_KEYS


def test_real_fixtures_show_the_baseline_location_has_availability():
    # The whole point of Finding 1: fsn1 (the baseline's own location) is not
    # actually empty, so Finding 2's "zero availability" note must not fire
    # for the real data.
    server_types_json = _load_fixture("server_types")
    offers = parse_offers(server_types_json)
    assert location_has_availability("fsn1", offers) is True
