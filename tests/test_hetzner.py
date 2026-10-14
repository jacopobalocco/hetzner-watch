import json
import pathlib

import httpx
import pytest

from hetzner_watch.hetzner import fetch, parse_baselines, parse_offers

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def servers_json():
    return load("servers")


@pytest.fixture
def server_types_json():
    return load("server_types")


def test_parse_baselines_reads_the_running_server(servers_json, server_types_json):
    baselines = parse_baselines(servers_json, server_types_json)
    assert len(baselines) == 1
    baseline = baselines[0]
    assert baseline.server_name == "example-server-1"
    assert baseline.location == "fsn1"
    assert baseline.specs.cores >= 1
    assert baseline.specs.memory_gb >= 1
    # Valori esatti della fixture per cpx12/fsn1: un controllo solo di segno
    # non fallirebbe se il parser prendesse per errore il price_entry di
    # un'altra location (es. hel1, anch'esso positivo).
    assert baseline.price_eur_net == pytest.approx(11.49)
    assert baseline.included_traffic_tib == pytest.approx(20.0)


def test_parse_baselines_picks_the_price_entry_for_the_servers_own_location():
    # Sintetico, non dalla fixture: nella fixture reale cpx12 ha prezzo e
    # traffico identici su fsn1/hel1/nbg1 (solo sin diverge), quindi una
    # regressione che sceglie la location sbagliata non farebbe fallire
    # nessun test basato su quella fixture. Tre price entry, tutte con
    # valori reciprocamente distinti e quella corretta (fsn1) in mezzo:
    # con solo due entry, "prices[0]" o "prices[-1]" avrebbe una probabilita'
    # su due di restituire comunque il valore atteso; con la corretta in
    # mezzo nessuna delle due mutazioni puo' sopravvivere per caso.
    servers_json = {
        "servers": [
            {
                "name": "synthetic-server",
                "location": {"name": "fsn1"},
                "server_type": {
                    "name": "synthetic-type",
                    "cores": 2,
                    "memory": 4,
                    "disk": 80,
                    "cpu_type": "shared",
                    "architecture": "x86",
                },
            }
        ]
    }
    server_types_json = {
        "server_types": [
            {
                "name": "synthetic-type",
                "prices": [
                    {
                        "location": "hel1",
                        "price_monthly": {"net": "999.99"},
                        "included_traffic": 1 * 1024**4,
                    },
                    {
                        "location": "fsn1",
                        "price_monthly": {"net": "11.49"},
                        "included_traffic": 20 * 1024**4,
                    },
                    {
                        "location": "nbg1",
                        "price_monthly": {"net": "555.55"},
                        "included_traffic": 5 * 1024**4,
                    },
                ],
            }
        ]
    }

    baselines = parse_baselines(servers_json, server_types_json)

    assert len(baselines) == 1
    baseline = baselines[0]
    assert baseline.price_eur_net == pytest.approx(11.49)
    assert baseline.included_traffic_tib == pytest.approx(20.0)


def test_parse_offers_expands_types_across_locations(server_types_json):
    offers = parse_offers(server_types_json)
    assert offers
    keys = {(o.type_name, o.location) for o in offers}
    assert len(keys) == len(offers), "una offerta per coppia tipo/location"
    assert {o.location for o in offers} >= {"fsn1", "nbg1", "hel1"}


def test_parse_offers_marks_availability_per_location(server_types_json):
    offers = parse_offers(server_types_json)
    assert any(o.available for o in offers)
    assert any(not o.available for o in offers), (
        "se tutto risulta disponibile il campo non discrimina: vedi docs/api-findings.md"
    )


def test_parse_offers_normalises_traffic_to_tebibytes(server_types_json):
    offers = parse_offers(server_types_json)
    assert all(0 < o.included_traffic_tib < 100 for o in offers)
    # 21990232555520 byte / 1024**4 = 20.0 esatto; / 1000**4 darebbe 21.99,
    # che passerebbe comunque il controllo di range sopra. Un valore esatto
    # e' l'unico modo per bloccare una regressione della base 1024 -> 1000.
    cpx12_fsn1 = next(
        o for o in offers if o.type_name == "cpx12" and o.location == "fsn1"
    )
    assert cpx12_fsn1.included_traffic_tib == pytest.approx(20.0)


def test_parse_offers_fsn1_availability_uses_the_type_level_signal(server_types_json):
    # docs/api-findings.md #6: datacenters[].server_types.available says 0 of
    # 24 for fsn1-dc14 (stale), but server_types[].locations[].available -
    # the same per-location signal parse_offers already reads for
    # deprecation - says cpx12 is available there, confirmed against the
    # real console (Finding 1: CX23/33/43 orderable in Falkenstein now).
    offers = parse_offers(server_types_json)
    cpx12_fsn1 = next(
        o for o in offers if o.type_name == "cpx12" and o.location == "fsn1"
    )
    assert cpx12_fsn1.available is True


def test_parse_offers_deprecated_is_per_location(server_types_json):
    # cpx11 (docs/api-findings.md #4): deprecated=false a livello di tipo, ma
    # deprecation non-null su fsn1/nbg1/hel1/sin e non su ash/hil. Il bool
    # type-level da solo lascerebbe passare le 4 location deprecate.
    offers = parse_offers(server_types_json)
    cpx11_by_location = {o.location: o for o in offers if o.type_name == "cpx11"}
    assert cpx11_by_location.keys() == {"ash", "fsn1", "hel1", "hil", "nbg1", "sin"}
    for location in ("fsn1", "nbg1", "hel1", "sin"):
        assert cpx11_by_location[location].deprecated is True, location
    for location in ("ash", "hil"):
        assert cpx11_by_location[location].deprecated is False, location


def test_parse_baselines_raises_a_clear_error_when_the_location_has_no_price():
    # Finding 7: a bare StopIteration (no server/location named) in an
    # unattended daily job is a traceback nobody can act on.
    servers_json = {
        "servers": [
            {
                "name": "orphan-server",
                "location": {"name": "atlantis"},
                "server_type": {
                    "name": "ghost-type",
                    "cores": 1,
                    "memory": 1,
                    "disk": 20,
                    "cpu_type": "shared",
                    "architecture": "x86",
                },
            }
        ]
    }
    server_types_json = {
        "server_types": [
            {
                "name": "ghost-type",
                "prices": [{"location": "fsn1", "price_monthly": {"net": "1.0"}}],
            }
        ]
    }

    with pytest.raises(ValueError, match="ghost-type"):
        parse_baselines(servers_json, server_types_json)
    with pytest.raises(ValueError, match="atlantis"):
        parse_baselines(servers_json, server_types_json)
    with pytest.raises(ValueError, match="orphan-server"):
        parse_baselines(servers_json, server_types_json)


def test_fetch_follows_pagination():
    pages = {
        1: {"server_types": [{"id": 1}], "meta": {"pagination": {"next_page": 2}}},
        2: {"server_types": [{"id": 2}], "meta": {"pagination": {"next_page": None}}},
    }
    empty = {"meta": {"pagination": {"next_page": None}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server_types"):
            page = int(request.url.params.get("page", 1))
            return httpx.Response(200, json=pages[page])
        key = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={key: [], **empty})

    servers, server_types = fetch(
        "tok", transport=httpx.MockTransport(handler)
    )
    assert [t["id"] for t in server_types["server_types"]] == [1, 2]


def test_fetch_never_calls_the_retired_datacenters_endpoint():
    # /v1/datacenters answers 410 Gone (observed 2026-10-14); a fetch that still
    # walks it fails the whole run before any availability is read.
    empty = {"meta": {"pagination": {"next_page": None}}}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        if key == "datacenters":
            return httpx.Response(410, json={"error": {"code": "gone"}})
        return httpx.Response(200, json={key: [], **empty})

    servers, server_types = fetch("tok", transport=httpx.MockTransport(handler))
    assert servers["servers"] == [] and server_types["server_types"] == []
