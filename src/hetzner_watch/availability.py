"""Alert once when a watched server type becomes orderable again.

The state lives in GitHub, not in a file: an open issue labelled for
(type, location) means "last seen available there". Opening a fresh issue is
what notifies; editing one does not, so a type that sells out closes its issue
and a later return opens a new one.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib

import yaml

from hetzner_watch import hetzner
from hetzner_watch.github import Issues

CONFIG_PATH = pathlib.Path(__file__).parent.parent.parent / "config.yaml"
OPEN = "open"
CLOSE = "close"
NOOP = "noop"


def location_availability(
    server_types_json: dict, type_name: str, locations: tuple[str, ...]
) -> dict[str, bool]:
    # server_types[].locations[].available is the authoritative signal
    # (docs/api-findings.md #6). A location missing from the type's list
    # cannot be ordered there.
    for server_type in server_types_json["server_types"]:
        if server_type["name"] == type_name:
            available = {
                loc["name"]: bool(loc.get("available"))
                for loc in server_type.get("locations", [])
            }
            return {location: available.get(location, False) for location in locations}
    # A typo in config.yaml would otherwise read as "sold out" forever.
    raise ValueError(f"tipo {type_name!r} assente dal catalogo Hetzner")


def decide(available: bool, open_issue: int | None) -> str:
    if available and open_issue is None:
        return OPEN
    if not available and open_issue is not None:
        return CLOSE
    return NOOP


def label(type_name: str, location: str) -> str:
    return f"disponibile:{type_name}@{location}"


def render_title(type_name: str, location: str) -> str:
    return f"{type_name} di nuovo disponibile in {location}"


def render_body(type_name: str, location: str, seen_at: str) -> str:
    return (
        f"`{type_name}` risulta ordinabile in `{location}` "
        f"(`GET /v1/server_types` → `locations[].available: true`, letto {seen_at}).\n\n"
        "Il job non ridimensiona nulla: se vuoi passarci, la procedura è in "
        "`docs/runbook-rescale.md`.\n\n"
        "Questa issue si chiude da sola quando il tipo torna esaurito; "
        "un ritorno successivo apre una issue nuova."
    )


def render_close_comment(type_name: str, location: str, seen_at: str) -> str:
    return f"`{type_name}` di nuovo esaurito in `{location}` (letto {seen_at})."


def main() -> None:
    seen_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    type_name = raw["watch_type"]
    locations = tuple(raw["locations"])
    # The catalogue is the same for every project: the first token is enough.
    token = os.environ["HCLOUD_TOKEN"].split(",")[0].strip()
    if not token:
        raise SystemExit("HCLOUD_TOKEN vuoto: il secret del repository manca")

    availability = location_availability(
        hetzner.fetch_server_types(token), type_name, locations
    )
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    with Issues(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]) as api:
        for location, available in availability.items():
            issue_label = label(type_name, location)
            open_issue = api.find_open(issue_label)
            action = decide(available, open_issue)
            if action == OPEN:
                # Assigned so the owner is notified as a participant: a bot's
                # new issue reaches only watchers, and the owner does not
                # watch this repository.
                api.create(
                    render_title(type_name, location),
                    render_body(type_name, location, seen_at),
                    issue_label,
                    assignees=[owner],
                )
            elif action == CLOSE:
                api.close(open_issue, render_close_comment(type_name, location, seen_at))
            print(f"{type_name}@{location}: available={available} → {action}")


if __name__ == "__main__":
    main()
