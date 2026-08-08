from __future__ import annotations

import datetime as dt
import os
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass

import yaml

from hetzner_watch import hetzner, report, snapshot
from hetzner_watch.compare import (
    Candidate,
    Config,
    find_candidates,
    location_has_availability,
)
from hetzner_watch.github import Issues
from hetzner_watch.hetzner import Baseline

LABEL = "hetzner-watch"
ROOT = pathlib.Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_PATH = ROOT / "snapshot.json"
NO_SERVERS_ERROR = "nessun server trovato: controllare i token HCLOUD_TOKEN"
# Finding 6: monitoring only the first server found is a deliberate scope cut
# (see README), but must be visible, not a silent drop of the rest.
MULTIPLE_SERVERS_WARNING = (
    "trovati {count} server ({names}); oggi ne viene confrontato solo uno: {chosen}"
)


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
    baseline_location_available: bool,
) -> Action:
    if not candidates:
        if open_issue is None:
            return Action("noop", "")
        return Action("close", report.render_close_comment(generated_at))
    body = report.render_body(
        baseline, candidates, changes, generated_at, baseline_location_available
    )
    if open_issue is None:
        return Action("create", body)
    if snapshot.has_changes(changes):
        return Action("update", body)
    return Action("noop", body)


def should_save(previous: snapshot.Snapshot | None, changes: snapshot.Changes) -> bool:
    return previous is None or snapshot.has_changes(changes)


def main() -> None:
    generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    config = load_config(CONFIG_PATH)
    tokens = [t.strip() for t in os.environ["HCLOUD_TOKEN"].split(",") if t.strip()]

    baselines: list[Baseline] = []
    offers: list[hetzner.Offer] = []
    for token in tokens:
        # datacenters payload is fetched but no longer parsed: its
        # available/supported lists are stale (docs/api-findings.md #6).
        # Kept for future diagnostics, dropped from parse_offers.
        servers_json, server_types_json, _datacenters_json = hetzner.fetch(token)
        baselines += hetzner.parse_baselines(servers_json, server_types_json)
        if not offers:
            offers = hetzner.parse_offers(server_types_json)

    if not baselines:
        raise SystemExit(NO_SERVERS_ERROR)
    baseline = baselines[0]
    if len(baselines) > 1:
        names = ", ".join(b.server_name for b in baselines)
        print(
            MULTIPLE_SERVERS_WARNING.format(
                count=len(baselines), names=names, chosen=baseline.server_name
            )
        )

    candidates = find_candidates(baseline, offers, config)
    baseline_location_available = location_has_availability(baseline.location, offers)
    previous = snapshot.load(SNAPSHOT_PATH)
    current = snapshot.build_snapshot(baseline, candidates, generated_at)
    changes = snapshot.diff(previous, current)

    with Issues(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]) as api:
        open_issue = api.find_open(LABEL)
        action = decide(
            open_issue, candidates, baseline, changes, generated_at,
            baseline_location_available,
        )
        if action.kind == "create":
            api.create(report.ISSUE_TITLE, action.body, LABEL)
        elif action.kind == "update":
            api.update(open_issue, action.body)
        elif action.kind == "close":
            api.close(open_issue, action.body)

    # generated_at is a fresh timestamp every run, so saving unconditionally
    # would dirty snapshot.json daily even when nothing changed, turning the
    # commit history (meant to read as a price time series) into noise.
    if should_save(previous, changes):
        snapshot.save(SNAPSHOT_PATH, current)

    print(f"{action.kind}: {len(candidates)} candidati")


if __name__ == "__main__":
    main()
