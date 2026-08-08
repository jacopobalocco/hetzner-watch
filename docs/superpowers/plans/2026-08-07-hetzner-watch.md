# hetzner-watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un job giornaliero su GitHub Actions che confronta il server Hetzner Cloud in uso contro il catalogo EU e apre/aggiorna una issue con le alternative fino a 20 € netti.

**Architecture:** Pipeline in quattro stadi — fetch (`hetzner.py`) → confronto (`compare.py`) → diff di stato (`snapshot.py`) → report e issue (`report.py`, `github.py`), messi insieme da `__main__.py`. Tutta la logica non banale vive in funzioni pure che prendono strutture dati e restituiscono strutture dati; la rete sta solo nei due moduli di I/O. Lo stato è un `snapshot.json` committato dal job.

**Tech Stack:** Python 3.12, uv, httpx, PyYAML, pytest. Nessun framework di mocking: fixture JSON reali per il parsing, `httpx.MockTransport` per i due moduli di rete.

## Global Constraints

- Soglia prezzo: **20,00 € netti/mese**, IVA esclusa — la stessa base in cui l'API espone i prezzi.
- Location ammesse: **`fsn1`, `nbg1`, `hel1`**. Nessuna location extra-UE.
- Token Hetzner: permesso **Read**, mai Read & Write. Il job non deve poter creare, ridimensionare o cancellare risorse.
- `HCLOUD_TOKEN` è una **lista separata da virgole** (un token per progetto Hetzner). I token stanno nei secret, mai in `config.yaml`.
- Un candidato è segnalato se ha **almeno una** metrica migliore del baseline. Le regressioni **non** escludono, ma devono comparire nel report con il segno.
- I tipi `deprecated` e quelli non `available` nel datacenter sono sempre esclusi.
- Il repo è **privato**.
- Nessun commento nel codice che spieghi *cosa* fa; solo *perché*, dove c'è un vincolo non ovvio.

---

### Task 1: Scaffold e cattura delle fixture reali

Questo task risponde alle domande lasciate aperte nella spec (§ *Da verificare in fase di implementazione*). Nessun altro task può iniziare prima, perché le strutture dati di Task 2 dipendono dai nomi di campo reali.

> **Prerequisito umano:** serve un token API Hetzner con permesso **Read**, creato da Console → Security → API tokens, esportato come `export HCLOUD_TOKEN=...`. Senza il token questo task non può essere completato: fermarsi e chiederlo.

**Files:**
- Create: `pyproject.toml`
- Create: `scripts/capture_fixtures.py`
- Create: `tests/fixtures/` (popolata dallo script)
- Create: `docs/api-findings.md`

**Interfaces:**
- Consumes: niente
- Produces: `tests/fixtures/servers.json`, `tests/fixtures/server_types.json`, `tests/fixtures/datacenters.json` — usate da tutti i test di Task 2.

- [ ] **Step 1: Creare `pyproject.toml`**

```toml
[project]
name = "hetzner-watch"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx>=0.27", "pyyaml>=6.0"]

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/hetzner_watch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Installare le dipendenze**

Run: `uv sync`
Expected: crea `.venv/` e `uv.lock` senza errori.

- [ ] **Step 3: Scrivere lo script di cattura**

Create `scripts/capture_fixtures.py`:

```python
"""Salva le risposte reali dell'API Hetzner come fixture di test."""

import json
import os
import pathlib
import re

import httpx

FIXTURES = pathlib.Path(__file__).parent.parent / "tests" / "fixtures"
BASE_URL = "https://api.hetzner.cloud/v1"
ENDPOINTS = ["servers", "server_types", "datacenters"]

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
IPV6 = re.compile(r"\b(?:[0-9a-f]{1,4}:){2,}[0-9a-f:/]+\b", re.IGNORECASE)


def redact(payload: str) -> str:
    return IPV6.sub("2001:db8::1", IPV4.sub("203.0.113.1", payload))


def fetch_all_pages(client: httpx.Client, endpoint: str) -> dict:
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


def main() -> None:
    token = os.environ["HCLOUD_TOKEN"].split(",")[0].strip()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        for endpoint in ENDPOINTS:
            body = fetch_all_pages(client, endpoint)
            target = FIXTURES / f"{endpoint}.json"
            target.write_text(redact(json.dumps(body, indent=2, sort_keys=True)))
            print(f"{target.name}: {len(body[endpoint])} elementi")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Eseguire la cattura**

Run: `uv run python scripts/capture_fixtures.py`
Expected: tre righe di output, es. `server_types.json: 27 elementi`. Se `servers.json` riporta 0 elementi, il token è del progetto sbagliato — fermarsi e chiedere.

- [ ] **Step 5: Rispondere alle domande aperte della spec**

Ispezionare le fixture e scrivere `docs/api-findings.md` con le risposte reali, non assunte:

```bash
uv run python -c "
import json,pathlib
f=pathlib.Path('tests/fixtures')
st=json.loads((f/'server_types.json').read_text())['server_types']
print('campi server_type:', sorted(st[0]))
print('campi prices[0]:', sorted(st[0]['prices'][0]))
print('esempio prezzo:', json.dumps(st[0]['prices'][0], indent=2))
srv=json.loads((f/'servers.json').read_text())['servers'][0]
print('baseline type:', json.dumps(srv['server_type'], indent=2)[:800])
print('datacenter:', srv['datacenter']['name'], '| location:', srv['datacenter']['location']['name'])
dc=json.loads((f/'datacenters.json').read_text())['datacenters'][0]
print('chiavi server_types nel datacenter:', sorted(dc['server_types']))
"
```

Documentare in `docs/api-findings.md`, una riga per punto:
1. Nome esatto del campo prezzo netto mensile e se è stringa o numero.
2. Le specs reali di **CPX12** (cores, memory, disk, cpu_type, architecture).
3. Se `included_traffic` sta dentro `prices[]` (per-location) o a livello di tipo, e la sua unità (byte o TB).
4. Se il flag di obsolescenza è `deprecated` (bool) o `deprecation` (oggetto/null).
5. Quante voci ha `datacenters[].server_types.available` rispetto a `supported` — se i due insiemi coincidono su tutti i datacenter, il campo non discrimina e va segnalato come problema prima di scrivere Task 3.

> **Gate:** se il punto 5 mostra che `available == supported` ovunque, fermarsi e riportarlo. Metà del valore del job poggia su quel campo.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock scripts/capture_fixtures.py tests/fixtures/ docs/api-findings.md
git commit -m "chore: scaffold e fixture reali dell'API Hetzner"
```

---

### Task 2: Client API e modello dati

**Files:**
- Create: `src/hetzner_watch/hetzner.py`
- Test: `tests/test_hetzner.py`

**Interfaces:**
- Consumes: le fixture di Task 1.
- Produces:
  - `Specs(cores: int, memory_gb: float, disk_gb: int, cpu_type: str, architecture: str)`
  - `Baseline(server_name: str, type_name: str, location: str, specs: Specs, price_eur_net: float, included_traffic_tb: float)`
  - `Offer(type_name: str, location: str, specs: Specs, price_eur_net: float, included_traffic_tb: float, deprecated: bool, available: bool)`
  - `parse_baselines(servers_json: dict, server_types_json: dict) -> list[Baseline]`
  - `parse_offers(server_types_json: dict, datacenters_json: dict) -> list[Offer]`
  - `fetch(token: str) -> tuple[dict, dict, dict]` — restituisce i tre payload grezzi `(servers, server_types, datacenters)`

Il seam che conta: `parse_*` sono pure e ricevono JSON già scaricato. `fetch` è l'unico pezzo che tocca la rete.

**Nota di dominio da rispettare:** i prezzi sono per **location** (`fsn1`), la disponibilità è per **datacenter** (`fsn1-dc14`). Una location ha più datacenter. Un tipo è disponibile in una location se è `available` in **almeno uno** dei suoi datacenter.

- [ ] **Step 1: Scrivere i test che falliscono**

Create `tests/test_hetzner.py`:

```python
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


@pytest.fixture
def datacenters_json():
    return load("datacenters")


def test_parse_baselines_reads_the_running_server(servers_json, server_types_json):
    baselines = parse_baselines(servers_json, server_types_json)
    assert len(baselines) == 1
    baseline = baselines[0]
    assert baseline.server_name == "example-server-1"
    assert baseline.location == "fsn1"
    assert baseline.specs.cores >= 1
    assert baseline.specs.memory_gb >= 1
    assert baseline.price_eur_net > 0


def test_parse_offers_expands_types_across_locations(server_types_json, datacenters_json):
    offers = parse_offers(server_types_json, datacenters_json)
    assert offers
    keys = {(o.type_name, o.location) for o in offers}
    assert len(keys) == len(offers), "una offerta per coppia tipo/location"
    assert {o.location for o in offers} >= {"fsn1", "nbg1", "hel1"}


def test_parse_offers_marks_availability_per_location(server_types_json, datacenters_json):
    offers = parse_offers(server_types_json, datacenters_json)
    assert any(o.available for o in offers)
    assert any(not o.available for o in offers), (
        "se tutto risulta disponibile il campo non discrimina: vedi docs/api-findings.md"
    )


def test_parse_offers_normalises_traffic_to_terabytes(server_types_json, datacenters_json):
    offers = parse_offers(server_types_json, datacenters_json)
    assert all(0 < o.included_traffic_tb < 100 for o in offers)


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

    servers, server_types, datacenters = fetch(
        "tok", transport=httpx.MockTransport(handler)
    )
    assert [t["id"] for t in server_types["server_types"]] == [1, 2]
```

- [ ] **Step 2: Verificare che falliscano**

Run: `uv run pytest tests/test_hetzner.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'hetzner_watch'`.

- [ ] **Step 3: Implementare `hetzner.py`**

Create `src/hetzner_watch/__init__.py` (vuoto) e `src/hetzner_watch/hetzner.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx

BASE_URL = "https://api.hetzner.cloud/v1"
BYTES_PER_TB = 1000**4


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
    included_traffic_tb: float


@dataclass(frozen=True)
class Offer:
    type_name: str
    location: str
    specs: Specs
    price_eur_net: float
    included_traffic_tb: float
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


def _is_deprecated(server_type: dict) -> bool:
    return bool(server_type.get("deprecated") or server_type.get("deprecation"))


def _traffic_tb(price_entry: dict, server_type: dict) -> float:
    raw = price_entry.get("included_traffic") or server_type.get("included_traffic") or 0
    return float(raw) / BYTES_PER_TB


def _price_net(price_entry: dict) -> float:
    return float(price_entry["price_monthly"]["net"])


def _available_by_location(datacenters_json: dict) -> dict[str, set[int]]:
    by_location: dict[str, set[int]] = {}
    for datacenter in datacenters_json["datacenters"]:
        location = datacenter["location"]["name"]
        available = set(datacenter["server_types"]["available"])
        by_location.setdefault(location, set()).update(available)
    return by_location


def parse_baselines(servers_json: dict, server_types_json: dict) -> list[Baseline]:
    prices_by_name = {t["name"]: t for t in server_types_json["server_types"]}
    baselines = []
    for server in servers_json["servers"]:
        server_type = server["server_type"]
        location = server["datacenter"]["location"]["name"]
        catalog_entry = prices_by_name.get(server_type["name"], server_type)
        price_entry = next(
            p for p in catalog_entry["prices"] if p["location"] == location
        )
        baselines.append(
            Baseline(
                server_name=server["name"],
                type_name=server_type["name"],
                location=location,
                specs=_specs(server_type),
                price_eur_net=_price_net(price_entry),
                included_traffic_tb=_traffic_tb(price_entry, catalog_entry),
            )
        )
    return baselines


def parse_offers(server_types_json: dict, datacenters_json: dict) -> list[Offer]:
    available = _available_by_location(datacenters_json)
    offers = []
    for server_type in server_types_json["server_types"]:
        for price_entry in server_type["prices"]:
            location = price_entry["location"]
            offers.append(
                Offer(
                    type_name=server_type["name"],
                    location=location,
                    specs=_specs(server_type),
                    price_eur_net=_price_net(price_entry),
                    included_traffic_tb=_traffic_tb(price_entry, server_type),
                    deprecated=_is_deprecated(server_type),
                    available=server_type["id"] in available.get(location, set()),
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


def fetch(
    token: str, transport: httpx.BaseTransport | None = None
) -> tuple[dict, dict, dict]:
    with httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        transport=transport,
    ) as client:
        return (
            _get_all_pages(client, "servers"),
            _get_all_pages(client, "server_types"),
            _get_all_pages(client, "datacenters"),
        )
```

- [ ] **Step 4: Verificare che passino**

Run: `uv run pytest tests/test_hetzner.py -v`
Expected: 5 PASS.

Se `test_parse_offers_marks_availability_per_location` fallisce perché tutto risulta disponibile, non aggirarlo: è il gate di Task 1 Step 5 che scatta. Fermarsi e riportare.

- [ ] **Step 5: Commit**

```bash
git add src/hetzner_watch/ tests/test_hetzner.py
git commit -m "feat: client API Hetzner e modello dati"
```

---

### Task 3: Motore di confronto

Il modulo con l'unica logica non banale del progetto. Nessun import di rete.

**Files:**
- Create: `src/hetzner_watch/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: `Baseline`, `Offer`, `Specs` da `hetzner_watch.hetzner`.
- Produces:
  - `Config(max_price_eur_net: float, locations: tuple[str, ...], include_arm: bool)`
  - `Delta(metric: str, baseline: float, candidate: float, better: bool)`
  - `Candidate(offer: Offer, deltas: tuple[Delta, ...], needs_multiarch: bool)`
  - `find_candidates(baseline: Baseline, offers: Sequence[Offer], config: Config) -> list[Candidate]`

**Regole, in ordine di applicazione:**
1. Scarta l'offerta che è lo stesso tipo nella stessa location del baseline.
2. Scarta `deprecated` o non `available`.
3. Scarta location fuori da `config.locations`.
4. Se `include_arm` è falso, scarta le architetture diverse da quella del baseline.
5. Scarta `price_eur_net > max_price_eur_net` (`<=` è incluso: 20,00 passa, 20,01 no).
6. Calcola i delta sulle metriche che **differiscono**. Se nessun delta è `better`, scarta.
7. `needs_multiarch` è vero se l'architettura differisce da quella del baseline.
8. Ordina per prezzo crescente, poi per nome del tipo.

`cpu_type` è confrontato via rango: `shared` = 0, `dedicated` = 1. Per `price` "migliore" significa **minore**; per tutte le altre metriche significa **maggiore**.

- [ ] **Step 1: Scrivere i test che falliscono**

Create `tests/test_compare.py`:

```python
import pytest

from hetzner_watch.compare import Config, find_candidates
from hetzner_watch.hetzner import Baseline, Offer, Specs

CONFIG = Config(max_price_eur_net=20.0, locations=("fsn1", "nbg1", "hel1"), include_arm=True)


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
        included_traffic_tb=20.0,
    )


def offer(name="CX99", location="fsn1", price=6.0, traffic=20.0, available=True,
          deprecated=False, **spec_kwargs):
    return Offer(
        type_name=name,
        location=location,
        specs=specs(**spec_kwargs),
        price_eur_net=price,
        included_traffic_tb=traffic,
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
        "cores", "disk_gb", "included_traffic_tb", "price_eur_net"
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


def test_dedicated_cpu_counts_as_an_improvement(baseline):
    offers = [offer(name="CCX13", price=13.0, cpu_type="dedicated")]
    candidates = find_candidates(baseline, offers, CONFIG)
    assert names(candidates) == ["CCX13"]
    assert ("cpu_type", True) in [(d.metric, d.better) for d in candidates[0].deltas]


def test_candidates_are_sorted_by_price_then_name(baseline):
    offers = [
        offer(name="C_MID", price=9.0, memory_gb=4.0),
        offer(name="A_CHEAP", price=6.0, memory_gb=4.0),
        offer(name="B_MID", price=9.0, memory_gb=4.0),
    ]
    assert names(find_candidates(baseline, offers, CONFIG)) == ["A_CHEAP", "B_MID", "C_MID"]
```

- [ ] **Step 2: Verificare che falliscano**

Run: `uv run pytest tests/test_compare.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'hetzner_watch.compare'`.

- [ ] **Step 3: Implementare `compare.py`**

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hetzner_watch.hetzner import Baseline, Offer

CPU_RANK = {"shared": 0, "dedicated": 1}
HIGHER_IS_BETTER = ("cores", "memory_gb", "disk_gb", "included_traffic_tb", "cpu_type")


@dataclass(frozen=True)
class Config:
    max_price_eur_net: float
    locations: tuple[str, ...]
    include_arm: bool


@dataclass(frozen=True)
class Delta:
    metric: str
    baseline: float
    candidate: float
    better: bool


@dataclass(frozen=True)
class Candidate:
    offer: Offer
    deltas: tuple[Delta, ...]
    needs_multiarch: bool


def _metric(source: Baseline | Offer, metric: str) -> float:
    if metric == "price_eur_net":
        return source.price_eur_net
    if metric == "included_traffic_tb":
        return source.included_traffic_tb
    if metric == "cpu_type":
        return float(CPU_RANK[source.specs.cpu_type])
    return float(getattr(source.specs, metric))


def _deltas(baseline: Baseline, offer: Offer) -> tuple[Delta, ...]:
    deltas = []
    for metric in (*HIGHER_IS_BETTER, "price_eur_net"):
        before, after = _metric(baseline, metric), _metric(offer, metric)
        if before == after:
            continue
        better = after < before if metric == "price_eur_net" else after > before
        deltas.append(Delta(metric=metric, baseline=before, candidate=after, better=better))
    return tuple(deltas)


def find_candidates(
    baseline: Baseline, offers: Sequence[Offer], config: Config
) -> list[Candidate]:
    candidates = []
    for offer in offers:
        if offer.type_name == baseline.type_name and offer.location == baseline.location:
            continue
        if offer.deprecated or not offer.available:
            continue
        if offer.location not in config.locations:
            continue
        needs_multiarch = offer.specs.architecture != baseline.specs.architecture
        if needs_multiarch and not config.include_arm:
            continue
        if offer.price_eur_net > config.max_price_eur_net:
            continue
        deltas = _deltas(baseline, offer)
        if not any(delta.better for delta in deltas):
            continue
        candidates.append(Candidate(offer, deltas, needs_multiarch))
    return sorted(candidates, key=lambda c: (c.offer.price_eur_net, c.offer.type_name))
```

- [ ] **Step 4: Verificare che passino**

Run: `uv run pytest tests/test_compare.py -v`
Expected: 13 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hetzner_watch/compare.py tests/test_compare.py
git commit -m "feat: motore di confronto baseline-catalogo"
```

---

### Task 4: Snapshot e diff

**Files:**
- Create: `src/hetzner_watch/snapshot.py`
- Test: `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `Baseline` da `hetzner.py`, `Candidate` da `compare.py`.
- Produces:
  - `Snapshot(generated_at: str, baseline: dict, candidates: dict[str, dict])`
  - `build_snapshot(baseline: Baseline, candidates: Sequence[Candidate], generated_at: str) -> Snapshot`
  - `Changes(new: tuple[str, ...], price_changed: tuple[tuple[str, float, float], ...], gone: tuple[str, ...])`
  - `diff(previous: Snapshot | None, current: Snapshot) -> Changes`
  - `has_changes(changes: Changes) -> bool`
  - `load(path: pathlib.Path) -> Snapshot | None` — `None` se il file non esiste
  - `save(path: pathlib.Path, snapshot: Snapshot) -> None`

La chiave di un candidato è `f"{type_name}@{location}"`.

- [ ] **Step 1: Scrivere i test che falliscono**

Create `tests/test_snapshot.py`:

```python
from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline, Offer, Specs
from hetzner_watch.snapshot import Snapshot, build_snapshot, diff, has_changes, load, save

SPECS = Specs(2, 2.0, 40, "shared", "x86")
BASELINE = Baseline("srv", "CPX12", "fsn1", SPECS, 5.0, 20.0)


def candidate(name="CAX21", location="fsn1", price=7.0):
    offer = Offer(name, location, SPECS, price, 20.0, deprecated=False, available=True)
    return Candidate(offer, (Delta("memory_gb", 2.0, 8.0, True),), needs_multiarch=False)


def snapshot(*candidates, generated_at="2026-08-07T07:00:00Z"):
    return build_snapshot(BASELINE, list(candidates), generated_at)


def test_snapshot_keys_are_type_at_location():
    assert set(snapshot(candidate()).candidates) == {"CAX21@fsn1"}


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


def test_a_different_timestamp_alone_is_not_a_change():
    before = snapshot(candidate(), generated_at="2026-08-06T07:00:00Z")
    after = snapshot(candidate(), generated_at="2026-08-07T07:00:00Z")
    assert has_changes(diff(before, after)) is False


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "snapshot.json"
    original = snapshot(candidate())
    save(path, original)
    assert load(path) == original


def test_load_returns_none_when_absent(tmp_path):
    assert load(tmp_path / "missing.json") is None
```

- [ ] **Step 2: Verificare che falliscano**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'hetzner_watch.snapshot'`.

- [ ] **Step 3: Implementare `snapshot.py`**

```python
from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from hetzner_watch.compare import Candidate
from hetzner_watch.hetzner import Baseline


@dataclass(frozen=True)
class Snapshot:
    generated_at: str
    baseline: dict
    candidates: dict[str, dict]


@dataclass(frozen=True)
class Changes:
    new: tuple[str, ...]
    price_changed: tuple[tuple[str, float, float], ...]
    gone: tuple[str, ...]


def key(type_name: str, location: str) -> str:
    return f"{type_name}@{location}"


def build_snapshot(
    baseline: Baseline, candidates: Sequence[Candidate], generated_at: str
) -> Snapshot:
    return Snapshot(
        generated_at=generated_at,
        baseline=asdict(baseline),
        candidates={
            key(c.offer.type_name, c.offer.location): {
                "price_eur_net": c.offer.price_eur_net,
                "cores": c.offer.specs.cores,
                "memory_gb": c.offer.specs.memory_gb,
                "disk_gb": c.offer.specs.disk_gb,
                "cpu_type": c.offer.specs.cpu_type,
                "architecture": c.offer.specs.architecture,
                "included_traffic_tb": c.offer.included_traffic_tb,
            }
            for c in candidates
        },
    )


def diff(previous: Snapshot | None, current: Snapshot) -> Changes:
    before = previous.candidates if previous else {}
    after = current.candidates
    price_changed = tuple(
        (k, before[k]["price_eur_net"], after[k]["price_eur_net"])
        for k in sorted(before.keys() & after.keys())
        if before[k]["price_eur_net"] != after[k]["price_eur_net"]
    )
    return Changes(
        new=tuple(sorted(after.keys() - before.keys())),
        price_changed=price_changed,
        gone=tuple(sorted(before.keys() - after.keys())),
    )


def has_changes(changes: Changes) -> bool:
    return bool(changes.new or changes.price_changed or changes.gone)


def load(path: pathlib.Path) -> Snapshot | None:
    if not path.exists():
        return None
    return Snapshot(**json.loads(path.read_text()))


def save(path: pathlib.Path, snapshot: Snapshot) -> None:
    path.write_text(json.dumps(asdict(snapshot), indent=2, sort_keys=True) + "\n")
```

- [ ] **Step 4: Verificare che passino**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hetzner_watch/snapshot.py tests/test_snapshot.py
git commit -m "feat: snapshot committabile e diff fra run"
```

---

### Task 5: Rendering del report

**Files:**
- Create: `src/hetzner_watch/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Baseline`, `Candidate`, `Changes`.
- Produces:
  - `render_body(baseline: Baseline, candidates: Sequence[Candidate], changes: Changes, generated_at: str) -> str`
  - `render_close_comment(generated_at: str) -> str`
  - `ISSUE_TITLE: str` — costante `"Alternative Hetzner sotto soglia"`

Ordine delle sezioni nel corpo, come da spec: **Novità dall'ultimo run** → **tabella completa** → **baseline e timestamp**.

Etichette leggibili delle metriche: `cores` → `vCPU`, `memory_gb` → `RAM`, `disk_gb` → `Disco`, `included_traffic_tb` → `Traffico`, `cpu_type` → `CPU`, `price_eur_net` → `Prezzo`.

- [ ] **Step 1: Scrivere i test che falliscono**

Create `tests/test_report.py`:

```python
from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline, Offer, Specs
from hetzner_watch.report import render_body, render_close_comment
from hetzner_watch.snapshot import Changes

SPECS = Specs(2, 2.0, 40, "shared", "x86")
BASELINE = Baseline("example-server-1", "CPX12", "fsn1", SPECS, 5.0, 20.0)
NO_CHANGES = Changes(new=(), price_changed=(), gone=())


def candidate(name="CAX21", price=7.0, multiarch=False, deltas=None):
    offer = Offer(name, "fsn1", SPECS, price, 20.0, deprecated=False, available=True)
    return Candidate(
        offer,
        deltas or (Delta("memory_gb", 2.0, 8.0, True), Delta("disk_gb", 40.0, 20.0, False)),
        needs_multiarch=multiarch,
    )


def body(*candidates, changes=NO_CHANGES):
    return render_body(BASELINE, list(candidates), changes, "2026-08-07T07:00:00Z")


def test_body_states_the_baseline_used():
    assert "CPX12" in body(candidate())
    assert "example-server-1" in body(candidate())
    assert "2026-08-07T07:00:00Z" in body(candidate())


def test_news_section_comes_before_the_full_table():
    changes = Changes(new=("CAX21@fsn1",), price_changed=(), gone=())
    rendered = body(candidate(), changes=changes)
    assert rendered.index("Novità") < rendered.index("Tutti i candidati")


def test_improvements_and_regressions_are_both_signed():
    rendered = body(candidate())
    assert "+6" in rendered
    assert "-20" in rendered


def test_multiarch_candidates_are_flagged():
    assert "multi-arch" in body(candidate(multiarch=True))
    assert "multi-arch" not in body(candidate(multiarch=False))


def test_price_changes_show_both_values():
    changes = Changes(new=(), price_changed=(("CAX21@fsn1", 7.0, 6.5),), gone=())
    rendered = body(candidate(price=6.5), changes=changes)
    assert "7.00" in rendered and "6.50" in rendered


def test_disappearances_are_listed():
    changes = Changes(new=(), price_changed=(), gone=("CCX13@fsn1",))
    assert "CCX13@fsn1" in body(candidate(), changes=changes)


def test_empty_candidate_list_says_so():
    assert "Nessuna alternativa" in body()


def test_close_comment_mentions_the_run_time():
    assert "2026-08-07T07:00:00Z" in render_close_comment("2026-08-07T07:00:00Z")
```

- [ ] **Step 2: Verificare che falliscano**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'hetzner_watch.report'`.

- [ ] **Step 3: Implementare `report.py`**

```python
from __future__ import annotations

from collections.abc import Sequence

from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline
from hetzner_watch.snapshot import Changes

ISSUE_TITLE = "Alternative Hetzner sotto soglia"
LABELS = {
    "cores": "vCPU",
    "memory_gb": "RAM",
    "disk_gb": "Disco",
    "included_traffic_tb": "Traffico",
    "cpu_type": "CPU",
    "price_eur_net": "Prezzo",
}
DISCLAIMER = (
    "Confronto automatico sul listino Hetzner Cloud. "
    "Verifica sempre specs e prezzo in console prima di migrare."
)


def _format_delta(delta: Delta) -> str:
    if delta.metric == "cpu_type":
        return "CPU dedicata" if delta.better else "CPU condivisa"
    if delta.metric == "price_eur_net":
        return f"Prezzo {delta.candidate - delta.baseline:+.2f} €"
    change = delta.candidate - delta.baseline
    return f"{LABELS[delta.metric]} {change:+g}"


def _row(candidate: Candidate) -> str:
    offer = candidate.offer
    changes = ", ".join(_format_delta(d) for d in candidate.deltas) or "—"
    note = "⚠️ richiede immagini multi-arch" if candidate.needs_multiarch else ""
    return (
        f"| `{offer.type_name}` | {offer.location} | {offer.price_eur_net:.2f} € | "
        f"{offer.specs.cores} | {offer.specs.memory_gb:g} GB | {offer.specs.disk_gb} GB | "
        f"{changes} | {note} |"
    )


def _news(changes: Changes) -> list[str]:
    lines = ["## Novità dall'ultimo run", ""]
    if not (changes.new or changes.price_changed or changes.gone):
        return [*lines, "Nessuna variazione.", ""]
    for entry in changes.new:
        lines.append(f"- 🆕 `{entry}`")
    for entry, before, after in changes.price_changed:
        direction = "📉" if after < before else "📈"
        lines.append(f"- {direction} `{entry}`: {before:.2f} € → {after:.2f} €")
    for entry in changes.gone:
        lines.append(f"- ❌ `{entry}` non è più un candidato")
    return [*lines, ""]


def render_body(
    baseline: Baseline,
    candidates: Sequence[Candidate],
    changes: Changes,
    generated_at: str,
) -> str:
    lines = _news(changes)
    lines += ["## Tutti i candidati", ""]
    if candidates:
        lines += [
            "| Tipo | Location | Prezzo/mese | vCPU | RAM | Disco | Delta | Note |",
            "|---|---|---|---|---|---|---|---|",
            *(_row(c) for c in candidates),
            "",
        ]
    else:
        lines += ["Nessuna alternativa sotto soglia al momento.", ""]
    lines += [
        "## Baseline",
        "",
        f"`{baseline.server_name}` — `{baseline.type_name}` in {baseline.location}: "
        f"{baseline.specs.cores} vCPU, {baseline.specs.memory_gb:g} GB RAM, "
        f"{baseline.specs.disk_gb} GB disco, {baseline.price_eur_net:.2f} €/mese netti.",
        "",
        f"_Run: {generated_at}. {DISCLAIMER}_",
    ]
    return "\n".join(lines)


def render_close_comment(generated_at: str) -> str:
    return (
        "Nessuna alternativa sotto soglia migliora il server attuale. "
        f"Chiudo automaticamente.\n\n_Run: {generated_at}_"
    )
```

- [ ] **Step 4: Verificare che passino**

Run: `uv run pytest tests/test_report.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hetzner_watch/report.py tests/test_report.py
git commit -m "feat: rendering markdown del report"
```

---

### Task 6: Ciclo di vita della issue GitHub

**Files:**
- Create: `src/hetzner_watch/github.py`
- Test: `tests/test_github.py`

**Interfaces:**
- Consumes: niente dai task precedenti (modulo autonomo di I/O).
- Produces:
  - `Issues(repo: str, token: str, transport: httpx.BaseTransport | None = None)` — context manager
  - `Issues.find_open(label: str) -> int | None`
  - `Issues.create(title: str, body: str, label: str) -> int`
  - `Issues.update(number: int, body: str) -> None`
  - `Issues.close(number: int, comment: str) -> None`

- [ ] **Step 1: Scrivere i test che falliscono**

Create `tests/test_github.py`:

```python
import httpx

from hetzner_watch.github import Issues


def recorder(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.read().decode() or None))
        return responses.pop(0)

    return handler, calls


def issues(handler):
    return Issues("me/hetzner-watch", "tok", transport=httpx.MockTransport(handler))


def test_find_open_returns_the_issue_number():
    handler, _ = recorder([httpx.Response(200, json=[{"number": 42}])])
    with issues(handler) as api:
        assert api.find_open("hetzner-watch") == 42


def test_find_open_returns_none_when_no_issue_is_open():
    handler, _ = recorder([httpx.Response(200, json=[])])
    with issues(handler) as api:
        assert api.find_open("hetzner-watch") is None


def test_create_posts_title_body_and_label():
    handler, calls = recorder([httpx.Response(201, json={"number": 7})])
    with issues(handler) as api:
        assert api.create("Titolo", "Corpo", "hetzner-watch") == 7
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/repos/me/hetzner-watch/issues")
    assert "Titolo" in payload and "Corpo" in payload and "hetzner-watch" in payload


def test_update_patches_only_the_body():
    handler, calls = recorder([httpx.Response(200, json={})])
    with issues(handler) as api:
        api.update(7, "Nuovo corpo")
    method, path, payload = calls[0]
    assert (method, path) == ("PATCH", "/repos/me/hetzner-watch/issues/7")
    assert "Nuovo corpo" in payload
    assert "state" not in payload


def test_close_comments_then_closes():
    handler, calls = recorder([httpx.Response(201, json={}), httpx.Response(200, json={})])
    with issues(handler) as api:
        api.close(7, "Chiudo")
    assert [(m, p) for m, p, _ in calls] == [
        ("POST", "/repos/me/hetzner-watch/issues/7/comments"),
        ("PATCH", "/repos/me/hetzner-watch/issues/7"),
    ]
    assert "closed" in calls[1][2]


def test_http_errors_are_raised():
    handler, _ = recorder([httpx.Response(403, json={"message": "Forbidden"})])
    with issues(handler) as api:
        try:
            api.find_open("hetzner-watch")
        except httpx.HTTPStatusError as error:
            assert error.response.status_code == 403
        else:
            raise AssertionError("un 403 deve sollevare, non passare in silenzio")
```

- [ ] **Step 2: Verificare che falliscano**

Run: `uv run pytest tests/test_github.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'hetzner_watch.github'`.

- [ ] **Step 3: Implementare `github.py`**

```python
from __future__ import annotations

import httpx

API_URL = "https://api.github.com"


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
            "GET", "/issues", params={"state": "open", "labels": label}
        )
        found = response.json()
        return found[0]["number"] if found else None

    def create(self, title: str, body: str, label: str) -> int:
        response = self._request(
            "POST", "/issues", json={"title": title, "body": body, "labels": [label]}
        )
        return int(response.json()["number"])

    def update(self, number: int, body: str) -> None:
        self._request("PATCH", f"/issues/{number}", json={"body": body})

    def close(self, number: int, comment: str) -> None:
        self._request("POST", f"/issues/{number}/comments", json={"body": comment})
        self._request("PATCH", f"/issues/{number}", json={"state": "closed"})
```

- [ ] **Step 4: Verificare che passino**

Run: `uv run pytest tests/test_github.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hetzner_watch/github.py tests/test_github.py
git commit -m "feat: ciclo di vita della issue GitHub"
```

---

### Task 7: Wiring dell'entrypoint

**Files:**
- Create: `src/hetzner_watch/__main__.py`
- Create: `config.yaml`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: tutto quanto prodotto dai Task 2-6.
- Produces:
  - `load_config(path: pathlib.Path) -> Config`
  - `Action(kind: str, body: str)` con `kind` in `{"create", "update", "close", "noop"}`
  - `decide(open_issue: int | None, candidates: Sequence[Candidate], baseline: Baseline, changes: Changes, generated_at: str) -> Action`
  - `main() -> None`

**Variabili d'ambiente lette da `main()`:** `HCLOUD_TOKEN` (lista separata da virgole), `GITHUB_TOKEN`, `GITHUB_REPOSITORY`.

**Tabella decisionale, da spec:**

| Candidati | Issue aperta | Cambiamenti | `kind` |
|---|---|---|---|
| sì | no | — | `create` |
| sì | sì | sì | `update` |
| sì | sì | no | `noop` |
| no | sì | — | `close` |
| no | no | — | `noop` |

- [ ] **Step 1: Creare `config.yaml`**

```yaml
max_price_eur_net: 20.0
locations: [fsn1, nbg1, hel1]
include_arm: true
```

- [ ] **Step 2: Scrivere i test che falliscono**

Create `tests/test_main.py`:

```python
import pathlib

from hetzner_watch.__main__ import decide, load_config
from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline, Offer, Specs
from hetzner_watch.snapshot import Changes

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
    action = decide(None, [CANDIDATE], BASELINE, SOME_CHANGES, "2026-08-07T07:00:00Z")
    assert action.kind == "create"
    assert "CAX21" in action.body


def test_candidates_with_changes_update_the_open_issue():
    action = decide(7, [CANDIDATE], BASELINE, SOME_CHANGES, "2026-08-07T07:00:00Z")
    assert action.kind == "update"


def test_candidates_without_changes_do_nothing():
    action = decide(7, [CANDIDATE], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z")
    assert action.kind == "noop"


def test_no_candidates_closes_the_open_issue():
    action = decide(7, [], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z")
    assert action.kind == "close"
    assert "2026-08-07T07:00:00Z" in action.body


def test_no_candidates_and_no_issue_does_nothing():
    action = decide(None, [], BASELINE, NO_CHANGES, "2026-08-07T07:00:00Z")
    assert action.kind == "noop"
```

- [ ] **Step 3: Verificare che falliscano**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'hetzner_watch.__main__'`.

- [ ] **Step 4: Implementare `__main__.py`**

```python
from __future__ import annotations

import datetime as dt
import os
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass

import yaml

from hetzner_watch import hetzner, report, snapshot
from hetzner_watch.compare import Candidate, Config, find_candidates
from hetzner_watch.github import Issues
from hetzner_watch.hetzner import Baseline

LABEL = "hetzner-watch"
ROOT = pathlib.Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_PATH = ROOT / "snapshot.json"


@dataclass(frozen=True)
class Action:
    kind: str
    body: str


def load_config(path: pathlib.Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    return Config(
        max_price_eur_net=float(raw["max_price_eur_net"]),
        locations=tuple(raw["locations"]),
        include_arm=bool(raw["include_arm"]),
    )


def decide(
    open_issue: int | None,
    candidates: Sequence[Candidate],
    baseline: Baseline,
    changes: snapshot.Changes,
    generated_at: str,
) -> Action:
    if not candidates:
        if open_issue is None:
            return Action("noop", "")
        return Action("close", report.render_close_comment(generated_at))
    body = report.render_body(baseline, candidates, changes, generated_at)
    if open_issue is None:
        return Action("create", body)
    if snapshot.has_changes(changes):
        return Action("update", body)
    return Action("noop", body)


def main() -> None:
    generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    config = load_config(CONFIG_PATH)
    tokens = [t.strip() for t in os.environ["HCLOUD_TOKEN"].split(",") if t.strip()]

    baselines: list[Baseline] = []
    offers: list[hetzner.Offer] = []
    for token in tokens:
        servers_json, server_types_json, datacenters_json = hetzner.fetch(token)
        baselines += hetzner.parse_baselines(servers_json, server_types_json)
        if not offers:
            offers = hetzner.parse_offers(server_types_json, datacenters_json)

    if not baselines:
        raise SystemExit("nessun server trovato: controllare i token HCLOUD_TOKEN")
    baseline = baselines[0]

    candidates = find_candidates(baseline, offers, config)
    current = snapshot.build_snapshot(baseline, candidates, generated_at)
    changes = snapshot.diff(snapshot.load(SNAPSHOT_PATH), current)

    with Issues(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]) as api:
        open_issue = api.find_open(LABEL)
        action = decide(open_issue, candidates, baseline, changes, generated_at)
        if action.kind == "create":
            api.create(report.ISSUE_TITLE, action.body, LABEL)
        elif action.kind == "update":
            api.update(open_issue, action.body)
        elif action.kind == "close":
            api.close(open_issue, action.body)

    snapshot.save(SNAPSHOT_PATH, current)
    print(f"{action.kind}: {len(candidates)} candidati")


if __name__ == "__main__":
    main()
```

> **Nota su più server:** `main()` usa il primo baseline. L'account ne ha uno solo; se un giorno ne avrà di più, il punto in cui estendere è questo e la spec lo prevede (una sezione di report per server).

- [ ] **Step 5: Verificare che passino**

Run: `uv run pytest -v`
Expected: tutti i test dei Task 2-7 passano — 47 in totale (5 hetzner + 13 compare + 9 snapshot + 8 report + 6 github + 6 main).

- [ ] **Step 6: Commit**

```bash
git add src/hetzner_watch/__main__.py config.yaml tests/test_main.py
git commit -m "feat: entrypoint e config"
```

---

### Task 8: Workflow GitHub Actions e README

**Files:**
- Create: `.github/workflows/daily.yml`
- Create: `README.md`

**Interfaces:**
- Consumes: `python -m hetzner_watch` da Task 7.
- Produces: il job schedulato. Nessuna interfaccia di codice.

- [ ] **Step 1: Creare il workflow**

Create `.github/workflows/daily.yml`:

```yaml
name: hetzner-watch

on:
  schedule:
    - cron: "0 7 * * *"
  workflow_dispatch:

permissions:
  contents: write
  issues: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"

      - run: uv sync

      - run: uv run pytest -q

      - name: Confronta il catalogo
        env:
          HCLOUD_TOKEN: ${{ secrets.HCLOUD_TOKEN }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
        run: uv run python -m hetzner_watch

      - name: Committa lo snapshot se cambiato
        run: |
          if [[ -n "$(git status --porcelain snapshot.json)" ]]; then
            git config user.name "hetzner-watch"
            git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
            git add snapshot.json
            git commit -m "chore: snapshot $(date -u +%F)"
            git push
          fi
```

- [ ] **Step 2: Creare il README**

Create `README.md`:

````markdown
# hetzner-watch

Job giornaliero che confronta il server Hetzner Cloud in uso contro il catalogo EU
e apre una issue quando esiste un'alternativa migliore fino a 20 € netti/mese.

## Come funziona

1. Legge il server attivo dall'API Hetzner (nessuna spec hardcoded: il baseline si
   aggiorna da solo se ridimensioni la macchina).
2. Scarica il catalogo e la disponibilità reale per datacenter.
3. Segnala i tipi sotto soglia che migliorano almeno una metrica, mostrando anche
   le regressioni con il segno.
4. Apre, aggiorna o chiude una issue con label `hetzner-watch`.

Nei giorni in cui l'insieme dei candidati non cambia, il job non scrive nulla.

## Setup

1. Crea un token API Hetzner con permesso **Read** (Console → Security → API tokens).
2. Salvalo come secret del repo: `HCLOUD_TOKEN`. Per coprire più progetti Hetzner,
   metti più token separati da virgola.
3. Il workflow gira alle 07:00 UTC; per lanciarlo a mano usa *Actions →
   hetzner-watch → Run workflow*.

Il token è read-only: il job non può creare, ridimensionare o cancellare risorse.

## Sviluppo

```bash
uv sync
uv run pytest
```

Per rigenerare le fixture dall'API reale:

```bash
export HCLOUD_TOKEN=...
uv run python scripts/capture_fixtures.py
```

## Config

`config.yaml`:

| Chiave | Significato |
|---|---|
| `max_price_eur_net` | Tetto di spesa in € **netti**/mese (IVA esclusa) |
| `locations` | Location Hetzner ammesse |
| `include_arm` | Se includere i tipi ARM (CAX), marcati `multi-arch` |
````

- [ ] **Step 3: Verificare la sintassi del workflow**

Run: `uv run python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/daily.yml').read_text()); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 4: Eseguire l'intera suite un'ultima volta**

Run: `uv run pytest -v`
Expected: 47 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/daily.yml README.md
git commit -m "feat: workflow giornaliero e documentazione"
```

---

## Dopo il piano: passi che richiedono l'utente

Non eseguibili da un agente senza conferma esplicita — vanno chiesti:

1. **Creare il repo remoto privato:** `gh repo create hetzner-watch --private --source=. --push`
2. **Caricare il secret:** `gh secret set HCLOUD_TOKEN` (token con permesso Read).
3. **Primo run manuale:** *Actions → hetzner-watch → Run workflow*. Aspettarsi una issue con ~10-20 righe — è il censimento previsto dalla spec, non un malfunzionamento.
4. **Dopo aver letto il primo output:** decidere come stringere la regola. Il punto di intervento è la condizione a Step 3 di Task 3 (`if not any(delta.better ...)`), più eventualmente `max_price_eur_net` in `config.yaml`.
