from __future__ import annotations

from dataclasses import dataclass

import httpx

BASE_URL = "https://api.hetzner.cloud/v1"
# included_traffic is reported in bytes, base 1024 (tebibytes), not base 1000:
# docs/api-findings.md confirms the values are exact multiples of 1024**4.
BYTES_PER_TIB = 1024**4


@dataclass(frozen=True)
class Specs:
    cores: int
    memory_gb: float
    disk_gb: int
    cpu_type: str
    architecture: str


@dataclass(frozen=True)
class Baseline:
    server_name: str
    type_name: str
    location: str
    specs: Specs
    price_eur_net: float
    included_traffic_tib: float


@dataclass(frozen=True)
class Offer:
    type_name: str
    location: str
    specs: Specs
    price_eur_net: float
    included_traffic_tib: float
    deprecated: bool
    available: bool


def _specs(server_type: dict) -> Specs:
    return Specs(
        cores=int(server_type["cores"]),
        memory_gb=float(server_type["memory"]),
        disk_gb=int(server_type["disk"]),
        cpu_type=server_type["cpu_type"],
        architecture=server_type["architecture"],
    )


def _is_type_deprecated(server_type: dict) -> bool:
    return bool(server_type.get("deprecated") or server_type.get("deprecation"))


def _is_location_deprecated(server_type: dict, location: str) -> bool:
    for loc in server_type.get("locations", []):
        if loc["name"] == location:
            return loc.get("deprecation") is not None
    return False


def _is_location_available(server_type: dict, location: str) -> bool:
    # docs/api-findings.md #6: datacenters[].server_types.available is stale
    # (fsn1-dc14 reads 0/24 while the owner can order CX23/33/43 there right
    # now). server_types[].locations[].available is the authoritative signal
    # - it's the same per-location list _is_location_deprecated already
    # reads, just a different key.
    for loc in server_type.get("locations", []):
        if loc["name"] == location:
            return bool(loc.get("available"))
    return False


def _traffic_tib(price_entry: dict, server_type: dict) -> float:
    raw = price_entry.get("included_traffic") or server_type.get("included_traffic") or 0
    return float(raw) / BYTES_PER_TIB


def _price_net(price_entry: dict) -> float:
    return float(price_entry["price_monthly"]["net"])


def parse_baselines(servers_json: dict, server_types_json: dict) -> list[Baseline]:
    catalog_by_name = {t["name"]: t for t in server_types_json["server_types"]}
    baselines = []
    for server in servers_json["servers"]:
        server_type = server["server_type"]
        location = server["location"]["name"]
        catalog_entry = catalog_by_name.get(server_type["name"], server_type)
        try:
            price_entry = next(
                p for p in catalog_entry["prices"] if p["location"] == location
            )
        except StopIteration:
            # A daily unattended job: a bare StopIteration is a traceback
            # with no server name, no type, no location (Finding 7).
            raise ValueError(
                f"nessun prezzo per il tipo {server_type['name']!r} in "
                f"{location!r} (server {server['name']!r})"
            ) from None
        baselines.append(
            Baseline(
                server_name=server["name"],
                type_name=server_type["name"],
                location=location,
                specs=_specs(server_type),
                price_eur_net=_price_net(price_entry),
                included_traffic_tib=_traffic_tib(price_entry, catalog_entry),
            )
        )
    return baselines


def parse_offers(server_types_json: dict) -> list[Offer]:
    offers = []
    for server_type in server_types_json["server_types"]:
        type_deprecated = _is_type_deprecated(server_type)
        for price_entry in server_type["prices"]:
            location = price_entry["location"]
            # The type-level bool can read false while the type is deprecated
            # only in this location (docs/api-findings.md #4); OR both signals
            # so Offer.deprecated stays correct without a downstream fix-up.
            deprecated = type_deprecated or _is_location_deprecated(
                server_type, location
            )
            offers.append(
                Offer(
                    type_name=server_type["name"],
                    location=location,
                    specs=_specs(server_type),
                    price_eur_net=_price_net(price_entry),
                    included_traffic_tib=_traffic_tib(price_entry, server_type),
                    deprecated=deprecated,
                    available=_is_location_available(server_type, location),
                )
            )
    return offers


def _get_all_pages(client: httpx.Client, endpoint: str) -> dict:
    page, merged = 1, None
    while True:
        response = client.get(f"/{endpoint}", params={"page": page, "per_page": 50})
        response.raise_for_status()
        body = response.json()
        if merged is None:
            merged = body
        else:
            merged[endpoint].extend(body[endpoint])
        next_page = body.get("meta", {}).get("pagination", {}).get("next_page")
        if not next_page:
            return merged
        page = next_page


def _client(token: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        transport=transport,
    )


# /datacenters is not fetched: it answers 410 Gone (observed 2026-10-14), and its
# availability lists were stale anyway (docs/api-findings.md #6, #7).
def fetch(
    token: str, transport: httpx.BaseTransport | None = None
) -> tuple[dict, dict]:
    with _client(token, transport) as client:
        return (
            _get_all_pages(client, "servers"),
            _get_all_pages(client, "server_types"),
        )


def fetch_server_types(
    token: str, transport: httpx.BaseTransport | None = None
) -> dict:
    with _client(token, transport) as client:
        return _get_all_pages(client, "server_types")
