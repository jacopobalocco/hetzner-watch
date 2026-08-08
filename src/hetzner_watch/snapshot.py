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
    spec_changed: tuple[str, ...] = ()
    baseline_changed: bool = False


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
                "included_traffic_tib": c.offer.included_traffic_tib,
            }
            for c in candidates
        },
    )


def _non_price(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "price_eur_net"}


def diff(previous: Snapshot | None, current: Snapshot) -> Changes:
    before = previous.candidates if previous else {}
    after = current.candidates
    common = sorted(before.keys() & after.keys())
    price_changed = tuple(
        (k, before[k]["price_eur_net"], after[k]["price_eur_net"])
        for k in common
        if before[k]["price_eur_net"] != after[k]["price_eur_net"]
    )
    # Candidate specs (disk, memory, ...) can change at an unchanged price;
    # without this a stale record could never self-heal, since price_changed
    # would stay empty forever (Finding 3).
    spec_changed = tuple(
        k for k in common if _non_price(before[k]) != _non_price(after[k])
    )
    # Snapshot.baseline was recorded but never compared (Finding 4): a resize
    # with an unchanged candidate set must still count as a change, or the
    # issue keeps showing the old baseline and its signed deltas forever.
    baseline_changed = previous is not None and previous.baseline != current.baseline
    return Changes(
        new=tuple(sorted(after.keys() - before.keys())),
        price_changed=price_changed,
        gone=tuple(sorted(before.keys() - after.keys())),
        spec_changed=spec_changed,
        baseline_changed=baseline_changed,
    )


def has_changes(changes: Changes) -> bool:
    return bool(
        changes.new
        or changes.price_changed
        or changes.gone
        or changes.spec_changed
        or changes.baseline_changed
    )


def load(path: pathlib.Path) -> Snapshot | None:
    if not path.exists():
        return None
    return Snapshot(**json.loads(path.read_text()))


def save(path: pathlib.Path, snapshot: Snapshot) -> None:
    path.write_text(json.dumps(asdict(snapshot), indent=2, sort_keys=True) + "\n")
