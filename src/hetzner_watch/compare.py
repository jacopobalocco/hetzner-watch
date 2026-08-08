from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hetzner_watch.hetzner import Baseline, Offer

# cpu_type has no natural numeric order; rank it so "better" comparisons can
# treat it like any other metric (dedicated > shared).
CPU_RANK = {"shared": 0, "dedicated": 1}
HIGHER_IS_BETTER = ("cores", "memory_gb", "disk_gb", "included_traffic_tib", "cpu_type")


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
    if metric == "included_traffic_tib":
        return source.included_traffic_tib
    if metric == "cpu_type":
        cpu_type = source.specs.cpu_type
        if cpu_type not in CPU_RANK:
            raise ValueError(
                f"cpu_type sconosciuto: {cpu_type!r} (attesi: {sorted(CPU_RANK)})"
            )
        return float(CPU_RANK[cpu_type])
    return float(getattr(source.specs, metric))


def _deltas(baseline: Baseline, offer: Offer) -> tuple[Delta, ...]:
    deltas = []
    for metric in (*HIGHER_IS_BETTER, "price_eur_net"):
        before, after = _metric(baseline, metric), _metric(offer, metric)
        if before == after:
            continue
        # price is the sole inverted metric: lower net cost is the improvement.
        better = after < before if metric == "price_eur_net" else after > before
        deltas.append(Delta(metric=metric, baseline=before, candidate=after, better=better))
    return tuple(deltas)


def location_has_availability(location: str, offers: Sequence[Offer]) -> bool:
    return any(o.available for o in offers if o.location == location)


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
