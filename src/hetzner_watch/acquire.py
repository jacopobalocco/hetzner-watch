"""Take the watched server type for the VM the moment it is orderable.

Runs every minute on the owner's Mac mini 2 (run_on_host), right after the availability mail
(launchd/com.hetzner-watch.availability.plist), with a Read & Write token. The
owner's decision of 2026-10-19: growing the VM comes before everything else —
production's job windows, jobs running on the VM, a gap in the data. So this
module is the one path in the repository allowed to create, resize and delete
servers, and only these: a placeholder of `watch_type`, and the VM named by
`vm_server_id`. The daily GitHub job stays read-only.

When `watch_type` is orderable in the VM's location:

1. A placeholder of that type is created at once. Holding it is what keeps the
   capacity while the VM is shut down and snapshotted.
2. The VM is shut down, snapshotted, and resized with the placeholder still
   held, so a second free slot costs nothing. Only if Hetzner answers that no
   slot is left is the placeholder deleted and the resize retried at once.
   Those seconds are the one moment the capacity can be lost; nothing short of
   migrating onto the placeholder closes them.
3. The VM is powered on whatever happened, and the owner gets a mail.

A run that dies between 1 and 2 leaves the placeholder labelled; the next run
finds it and goes straight to 2.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import subprocess
import time
from enum import Enum

import httpx
import yaml

from hetzner_watch import hetzner
from hetzner_watch.availability import location_availability, on_this_host, send_mail

ROOT = pathlib.Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"
# Written when a swap raises; while it exists no run touches the VM again, so a
# fault that repeats cannot shut production down every five minutes.
HALT_PATH = ROOT / "acquire-halted"
PLACEHOLDER_LABEL = "hetzner-watch=placeholder"
POLL_S = 5
ACTION_TIMEOUT_S = 1800
SHUTDOWN_TIMEOUT_S = 300
HEALTH_TRIES = 12
# Hetzner's error codes for "this type cannot be placed here right now".
NO_CAPACITY = {"resource_unavailable", "server_type_unavailable", "placement_error"}


class Step(Enum):
    DONE = "done"  # the VM already has the target type
    WAIT = "wait"  # not orderable, nothing held
    RESERVE = "reserve"  # orderable: take a placeholder, then swap
    SWAP = "swap"  # a placeholder is already held: swap


def next_step(vm_type: str, target: str, holding: bool, available: bool) -> Step:
    if vm_type == target:
        return Step.DONE
    if holding:
        return Step.SWAP
    return Step.RESERVE if available else Step.WAIT


def no_capacity(error: dict | None) -> bool:
    return bool(error) and error.get("code") in NO_CAPACITY


class Api:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self.c = hetzner._client(token, transport)

    def get(self, path: str, **params) -> dict:
        r = self.c.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict | None = None) -> dict:
        """The body, or {"error": ...} on a refusal, so a caller can tell no-capacity apart."""
        r = self.c.post(path, json=body or {})
        if r.status_code >= 400:
            return {"error": r.json().get("error", {"code": str(r.status_code)})}
        return r.json()

    def delete(self, path: str) -> None:
        self.c.delete(path).raise_for_status()

    def wait(self, action: dict, timeout: int = ACTION_TIMEOUT_S) -> dict | None:
        """None on success, the action's error otherwise."""
        deadline = time.monotonic() + timeout
        while True:
            a = self.get(f"/actions/{action['id']}")["action"]
            if a["status"] != "running":
                return None if a["status"] == "success" else (a.get("error") or {"code": "failed"})
            if time.monotonic() > deadline:
                return {"code": "timeout", "message": f"action {a['id']} still running"}
            time.sleep(POLL_S)

    def server(self, sid: int) -> dict:
        return self.get(f"/servers/{sid}")["server"]

    def placeholders(self) -> list[dict]:
        return self.get("/servers", label_selector=PLACEHOLDER_LABEL)["servers"]


def reserve(api: Api, type_name: str, location: str) -> dict:
    # IPv6 only: a primary IPv4 is billed, and the placeholder is never reached.
    r = api.post("/servers", {
        "name": f"placeholder-{type_name}",
        "server_type": type_name,
        "image": "ubuntu-24.04",
        "location": location,
        "start_after_create": False,
        "labels": {"hetzner-watch": "placeholder"},
        "public_net": {"enable_ipv4": False, "enable_ipv6": True},
    })
    if "error" in r:
        raise RuntimeError(f"placeholder not created: {r['error']}")
    if err := api.wait(r["action"]):
        raise RuntimeError(f"placeholder create failed: {err}")
    return r["server"]


def resize(api: Api, sid: int, type_name: str) -> dict | None:
    r = api.post(f"/servers/{sid}/actions/change_type", {"server_type": type_name, "upgrade_disk": True})
    return r["error"] if "error" in r else api.wait(r["action"])


def swap(api: Api, sid: int, type_name: str, placeholder_id: int) -> tuple[bool, list[str]]:
    """Resize the VM. Returns (resized, log). The VM is running on return either way."""
    log: list[str] = []
    t0 = time.monotonic()

    def note(line: str) -> None:
        log.append(f"{time.monotonic() - t0:4.0f}s  {line}")
        print(log[-1], flush=True)

    try:
        r = api.post(f"/servers/{sid}/actions/shutdown")
        if "error" in r:
            raise RuntimeError(f"shutdown refused: {r['error']}")
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_S
        while api.server(sid)["status"] != "off":
            if time.monotonic() > deadline:
                # The OS ignored ACPI: never force a power-off under a database.
                raise RuntimeError("VM still on after ACPI shutdown; investigate over SSH")
            time.sleep(POLL_S)
        note("VM off")

        # The one way back from upgrade_disk, which cannot be undone.
        snap = api.post(f"/servers/{sid}/actions/create_image",
                        {"type": "snapshot", "description": f"pre-rescale to {type_name} {dt.date.today()}"})
        if "error" in snap or (err := api.wait(snap["action"])):
            raise RuntimeError(f"snapshot failed: {snap.get('error') or err}")
        note(f"snapshot {snap['image']['id']}")

        err = resize(api, sid, type_name)
        if no_capacity(err):
            note(f"no second slot ({err['code']}): releasing the placeholder and retrying")
            api.delete(f"/servers/{placeholder_id}")
            err = resize(api, sid, type_name)
        elif err:
            # Not a capacity refusal: the placeholder is still the capacity we hold.
            raise RuntimeError(f"resize failed, placeholder kept: {err}")
        else:
            api.delete(f"/servers/{placeholder_id}")
        note(f"resize failed: {err}" if err else f"resized to {type_name}, placeholder deleted")
        return err is None, log
    finally:
        if api.server(sid)["status"] != "running":
            api.wait(api.post(f"/servers/{sid}/actions/poweron")["action"])
            note("VM powered on")


def healthy(url: str) -> bool:
    for _ in range(HEALTH_TRIES):
        try:
            if httpx.get(url, timeout=15).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(10)
    return False


def shell(command: str) -> str:
    p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    return (p.stdout + p.stderr).strip()


def mail(subject: str, lines: list[str], app_password: str) -> None:
    # Imported here: mailstile is the optional `mail` extra, absent from CI.
    import mailstile as st

    html = st.pagina(
        occhiello="HETZNER-WATCH · ACQUISTO",
        titolo=subject,
        sottotitolo=st.data_it(dt.datetime.now().astimezone()),
        blocchi=[st.card("".join(st.testo(line) for line in lines))],
    )
    send_mail(f"[hetzner-watch] {subject}", "\n".join(lines) + "\n", html, app_password)


def main() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    if not on_this_host(raw):
        return
    target, sid = raw["watch_type"], int(raw["vm_server_id"])
    token = os.environ.get("HCLOUD_RW_TOKEN", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not token or not app_password:
        raise SystemExit("HCLOUD_RW_TOKEN o GMAIL_APP_PASSWORD vuoti: manca .env (secrets-sync pull)")
    if HALT_PATH.exists():
        raise SystemExit(f"fermo dopo un errore: leggi {HALT_PATH}, poi cancellalo per riprendere")
    api = Api(token)

    vm = api.server(sid)
    vm_type = vm["server_type"]["name"]
    held = api.placeholders()
    if len(held) > 1:
        raise SystemExit(f"{len(held)} segnaposto: ne è previsto al massimo uno, controlla a mano")
    # Servers carry a location only since /datacenters was retired (docs/api-findings.md #7).
    location = vm["location"]["name"]
    available = (
        vm_type != target
        and not held
        and location_availability(api.get("/server_types", name=target), target, (location,))[location]
    )
    step = next_step(vm_type, target, bool(held), available)
    print(f"{dt.datetime.now().astimezone():%Y-%m-%dT%H:%M%z} {vm_type} -> {target} {step.value}", flush=True)

    if step is Step.DONE and held:
        api.delete(f"/servers/{held[0]['id']}")
        return
    if step is Step.RESERVE:
        held = [reserve(api, target, location)]
        print(f"placeholder {held[0]['id']} held", flush=True)
    elif step is not Step.SWAP:
        return

    try:
        ok, log = swap(api, sid, target, held[0]["id"])
    except Exception as exc:
        HALT_PATH.write_text(f"{dt.datetime.now().isoformat()} {exc!r}\n")
        mail("scambio interrotto da un errore, job fermo", [
            repr(exc),
            "La VM è stata riaccesa. Il segnaposto, se c'è, è ancora tenuto.",
            f"Il job non tocca più niente finché non cancelli {HALT_PATH.name}.",
        ], app_password)
        raise
    if ok:
        up = healthy(raw["health_url"])
        mail(f"VM ridimensionata a {target}" + ("" if up else ": il sito NON risponde"), [
            *log,
            f"{raw['health_url']}: " + ("200" if up else "nessuna risposta 200 in due minuti"),
            shell(raw["disk_command"]),
            "I job interrotti vanno rilanciati a mano. Lo snapshot resta finché non lo cancelli.",
        ], app_password)
    else:
        mail(f"{target} perso nello scambio", [
            *log,
            f"La VM è di nuovo accesa, ancora {vm_type}. Il job riprova al prossimo {target} disponibile.",
        ], app_password)


if __name__ == "__main__":
    main()
