"""Mail the owner once when a watched server type turns orderable again.

Runs hourly on the owner's Mac from a LaunchAgent
(launchd/com.hetzner-watch.availability.plist), not from GitHub Actions: a
public repository's schedule is switched off after 60 days without commits,
and main's protection refuses the bot's commits, so the job could not keep
itself alive there. The state is a local file holding the availability last
seen per location; a mail goes out only on a sold-out -> available turn.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import smtplib
from email.message import EmailMessage

import yaml

from hetzner_watch import hetzner

ROOT = pathlib.Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "availability-state.json"
MAIL_USER = "jacopo.balocco90@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


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


def newly_available(previous: dict[str, bool], current: dict[str, bool]) -> list[str]:
    # A location never seen before counts as sold out: on the first run a type
    # that is already orderable is news worth one mail.
    return [loc for loc, ok in current.items() if ok and not previous.get(loc, False)]


def load_state(path: pathlib.Path) -> dict[str, bool]:
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(path: pathlib.Path, state: dict[str, bool]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def render_mail(
    type_name: str, locations: list[str], vm_location: str, seen_at: dt.datetime
) -> tuple[str, str, str]:
    # Imported here: mailstile is the optional `mail` extra, absent from CI.
    import mailstile as st

    where = ", ".join(locations)
    subject = f"[hetzner-watch] {type_name} di nuovo disponibile in {where}"
    if vm_location in locations:
        verdict = f"Disponibile anche in {vm_location}, dove sta la VM: il ridimensionamento è possibile."
    else:
        verdict = (
            f"Non in {vm_location}, dove sta la VM: un ridimensionamento resta nella "
            "stessa sede, quindi per ora non cambia niente."
        )
    footer = (
        "Il job non ridimensiona nulla: la procedura è in docs/runbook-rescale.md. "
        f"Letto da GET /v1/server_types il {st.data_it(seen_at)} alle "
        f"{seen_at:%H:%M}. Un nuovo avviso arriva solo se il tipo si esaurisce e torna."
    )
    text = f"{type_name} è di nuovo ordinabile in {where}.\n\n{verdict}\n\n{footer}\n"
    html = st.pagina(
        occhiello="HETZNER-WATCH · DISPONIBILITÀ",
        titolo=f"{type_name} di nuovo disponibile",
        sottotitolo=st.data_it(seen_at),
        blocchi=[
            st.card(
                st.testo(f"Ordinabile in <b>{where}</b>.", colore=st.INK, dimensione=15)
                + st.testo(verdict)
            ),
            st.pie(footer),
        ],
    )
    return subject, text, html


def send_mail(subject: str, text: str, html: str, app_password: str) -> None:
    # Raises on failure: main() then leaves the state unsaved, so the next run
    # tries the same mail again instead of losing it.
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, MAIL_USER, MAIL_USER
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(MAIL_USER, app_password)
        smtp.send_message(msg)


def main() -> None:
    seen_at = dt.datetime.now().astimezone()
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    type_name = raw["watch_type"]
    vm_location = raw["vm_location"]
    locations = tuple(raw["locations"])
    token = os.environ.get("HCLOUD_TOKEN", "").split(",")[0].strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not token or not app_password:
        raise SystemExit("HCLOUD_TOKEN o GMAIL_APP_PASSWORD vuoti: manca .env (secrets-sync pull)")

    current = location_availability(
        hetzner.fetch_server_types(token), type_name, locations
    )
    previous = load_state(STATE_PATH)
    fresh = newly_available(previous, current)
    if fresh:
        send_mail(*render_mail(type_name, fresh, vm_location, seen_at), app_password)
    if current != previous:
        save_state(STATE_PATH, current)
    print(f"{seen_at:%Y-%m-%dT%H:%M%z} {type_name} {current} mail={fresh or 'no'}")


if __name__ == "__main__":
    main()
