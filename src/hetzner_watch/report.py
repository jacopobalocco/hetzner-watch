from __future__ import annotations

from collections.abc import Sequence

from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline
from hetzner_watch.snapshot import Changes, has_changes

ISSUE_TITLE = "Alternative Hetzner sotto soglia"

# Keyed on Delta.metric, which carries compare.py's spelling: traffic comes
# from hetzner.py's BYTES_PER_TIB (base 1024), so this is included_traffic_tib
# and the rendered unit must say TiB, never TB.
LABELS = {
    "cores": "vCPU",
    "memory_gb": "RAM",
    "disk_gb": "Disco",
    "included_traffic_tib": "Traffico",
    "cpu_type": "CPU",
    "price_eur_net": "Prezzo",
}

DISCLAIMER = (
    "Confronto automatico sul listino Hetzner Cloud. "
    "Verifica sempre specs e prezzo in console prima di migrare."
)
CLOSE_COMMENT_TEXT = (
    "Nessuna alternativa sotto soglia migliora il server attuale. Chiudo automaticamente."
)

NEWS_HEADING = "## Novità dall'ultimo run"
TABLE_HEADING = "## Tutti i candidati"
BASELINE_HEADING = "## Baseline"
NO_NEWS_TEXT = "Nessuna variazione."
NO_CANDIDATES_TEXT = "Nessuna alternativa sotto soglia al momento."
TABLE_HEADER = "| Tipo | Location | Prezzo/mese | vCPU | RAM | Disco | Delta | Note |"
TABLE_SEPARATOR = "|---|---|---|---|---|---|---|---|"
MULTIARCH_NOTE = "⚠️ richiede immagini multi-arch"
BASELINE_LOCATION_EMPTY_TEXT = (
    "⚠️ Nessun tipo attualmente disponibile in {location}: "
    "i candidati mostrati provengono da altre location."
)

CPU_DEDICATED_LABEL = "CPU dedicata"
CPU_SHARED_LABEL = "CPU condivisa"
NEW_CANDIDATE_ICON = "🆕"
PRICE_DROP_ICON = "📉"
PRICE_RISE_ICON = "📈"
GONE_CANDIDATE_ICON = "❌"
GONE_CANDIDATE_TEXT = "non è più un candidato"
SPEC_CHANGED_ICON = "🔧"
SPEC_CHANGED_TEXT = "specifiche cambiate"
BASELINE_CHANGED_ICON = "🔁"
BASELINE_CHANGED_TEXT = "baseline cambiato: ora"


def _format_delta(delta: Delta) -> str:
    change = delta.candidate - delta.baseline
    if delta.metric == "cpu_type":
        return CPU_DEDICATED_LABEL if delta.better else CPU_SHARED_LABEL
    if delta.metric == "price_eur_net":
        return f"{LABELS['price_eur_net']} {change:+.2f} €"
    if delta.metric == "included_traffic_tib":
        return f"{LABELS['included_traffic_tib']} {change:+g} TiB"
    return f"{LABELS[delta.metric]} {change:+g}"


def _row(candidate: Candidate) -> str:
    offer = candidate.offer
    deltas = ", ".join(_format_delta(d) for d in candidate.deltas) or "—"
    note = MULTIARCH_NOTE if candidate.needs_multiarch else ""
    return (
        f"| `{offer.type_name}` | {offer.location} | {offer.price_eur_net:.2f} € | "
        f"{offer.specs.cores} | {offer.specs.memory_gb:g} GB | {offer.specs.disk_gb} GB | "
        f"{deltas} | {note} |"
    )


def _news_lines(changes: Changes, baseline: Baseline) -> list[str]:
    lines = [NEWS_HEADING, ""]
    if not has_changes(changes):
        return [*lines, NO_NEWS_TEXT, ""]
    if changes.baseline_changed:
        lines.append(
            f"- {BASELINE_CHANGED_ICON} {BASELINE_CHANGED_TEXT} "
            f"`{baseline.type_name}` in {baseline.location}"
        )
    for entry in changes.new:
        lines.append(f"- {NEW_CANDIDATE_ICON} `{entry}`")
    for entry, before, after in changes.price_changed:
        direction = PRICE_DROP_ICON if after < before else PRICE_RISE_ICON
        lines.append(f"- {direction} `{entry}`: {before:.2f} € → {after:.2f} €")
    for entry in changes.spec_changed:
        lines.append(f"- {SPEC_CHANGED_ICON} `{entry}` {SPEC_CHANGED_TEXT}")
    for entry in changes.gone:
        lines.append(f"- {GONE_CANDIDATE_ICON} `{entry}` {GONE_CANDIDATE_TEXT}")
    return [*lines, ""]


def render_body(
    baseline: Baseline,
    candidates: Sequence[Candidate],
    changes: Changes,
    generated_at: str,
    baseline_location_available: bool,
) -> str:
    lines = _news_lines(changes, baseline)
    lines += [TABLE_HEADING, ""]
    if not baseline_location_available:
        lines += [BASELINE_LOCATION_EMPTY_TEXT.format(location=baseline.location), ""]
    if candidates:
        lines += [
            TABLE_HEADER,
            TABLE_SEPARATOR,
            *(_row(c) for c in candidates),
            "",
        ]
    else:
        lines += [NO_CANDIDATES_TEXT, ""]
    lines += [
        BASELINE_HEADING,
        "",
        f"`{baseline.server_name}` — `{baseline.type_name}` in {baseline.location}: "
        f"{baseline.specs.cores} vCPU, {baseline.specs.memory_gb:g} GB RAM, "
        f"{baseline.specs.disk_gb} GB disco, {baseline.price_eur_net:.2f} €/mese netti.",
        "",
        f"_Run: {generated_at}. {DISCLAIMER}_",
    ]
    return "\n".join(lines)


def render_close_comment(generated_at: str) -> str:
    return f"{CLOSE_COMMENT_TEXT}\n\n_Run: {generated_at}_"
