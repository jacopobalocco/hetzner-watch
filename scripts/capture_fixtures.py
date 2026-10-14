"""Salva le risposte reali dell'API Hetzner come fixture di test."""

import json
import os
import pathlib
import re

from hetzner_watch.hetzner import fetch

FIXTURES = pathlib.Path(__file__).parent.parent / "tests" / "fixtures"
ENDPOINTS = ["servers", "server_types"]

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
IPV6 = re.compile(r"\b(?:[0-9a-f]{1,4}:){2,}[0-9a-f:/]+\b", re.IGNORECASE)


def redact(payload: str) -> str:
    return IPV6.sub("2001:db8::1", IPV4.sub("203.0.113.1", payload))


def main() -> None:
    token = os.environ["HCLOUD_TOKEN"].split(",")[0].strip()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    bodies = fetch(token)
    for endpoint, body in zip(ENDPOINTS, bodies):
        target = FIXTURES / f"{endpoint}.json"
        target.write_text(redact(json.dumps(body, indent=2, sort_keys=True)))
        print(f"{target.name}: {len(body[endpoint])} elementi")


if __name__ == "__main__":
    main()
