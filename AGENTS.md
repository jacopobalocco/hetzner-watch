# AGENTS.md

hetzner-watch è un job orario (GitHub Actions) che confronta un server Hetzner
Cloud con il catalogo Hetzner e segnala alternative migliori o più economiche
aprendo/aggiornando/chiudendo una issue GitHub. **Vincolo rigido, non negoziabile:**
il job legge soltanto (token Hetzner con permesso Read) e non deve mai acquisire
la capacità di modificare risorse Hetzner — non creare, ridimensionare o cancellare
nulla, in nessun percorso di codice, per nessuna ragione. È decision support, punto:
la issue segnala, un umano decide e agisce (vedi `docs/runbook-rescale.md`).

## Build · test

```bash
uv sync            # installa le dipendenze (verificato: 15 pacchetti risolti)
uv run pytest       # 94 test, verificato green
uv run pytest -q    # stessa cosa, output compatto
```

Non c'è un linter configurato in questo repo (nessun ruff/black/flake8/mypy in
`pyproject.toml`): non inventarne l'uso, non aggiungerne uno se non richiesto
esplicitamente.

## Module map

| File | Possiede | Confine |
|---|---|---|
| `src/hetzner_watch/hetzner.py` | Modello (`Specs`, `Baseline`, `Offer`), parsing delle risposte API, fetch HTTP | `fetch()` è IO (httpx, `transport` iniettabile nei test); `parse_baselines`/`parse_offers` sono pure funzioni dict → dataclass |
| `src/hetzner_watch/compare.py` | Motore di confronto: baseline × offerte → candidati con delta firmati | **Puro.** Nessuna rete, nessuna chiamata GitHub. È il modulo con la logica non banale; tutti i test che contano vivono qui |
| `src/hetzner_watch/snapshot.py` | Stato (`Snapshot`), diff rispetto al run precedente | `build_snapshot`/`diff`/`has_changes` sono pure; `load`/`save` sono IO su file |
| `src/hetzner_watch/report.py` | Rendering markdown della issue | Puro: stringhe in, stringa fuori |
| `src/hetzner_watch/github.py` | Client Issues (find/create/update/close) | IO (httpx, `transport` iniettabile nei test) |
| `src/hetzner_watch/availability.py` | Avviso di ritorno in disponibilità di `watch_type`, una issue per location | Entry point a sé (`python -m hetzner_watch.availability`), passo separato del workflow; lo stato è la issue aperta, nessun file |
| `src/hetzner_watch/__main__.py` | Wiring: legge env/config, chiama gli altri moduli, decide l'azione | L'unico punto dove IO e logica pura si incontrano |
| `scripts/capture_fixtures.py` | Cattura le fixture da un account Hetzner reale, redige gli IP | IO, va eseguito a mano, mai nei test |

Il confine pure/IO è deliberato: `compare.py`, `report.py` e la parte pura di
`snapshot.py`/`hetzner.py` non toccano rete né GitHub, quindi girano nei test senza
alcun mock. `hetzner.fetch` e `github.Issues` sono gli unici due collaboratori con
IO reale, entrambi con un parametro `transport` per la sostituzione nei test
(`httpx.MockTransport`).

## Test policy

Fixture-driven, nessuna chiamata di rete nei test, nessun framework di mocking
(solo `httpx.MockTransport` per i due client HTTP, e fixture JSON reali in
`tests/fixtures/` per il parsing). Ogni modulo IO ha un punto di iniezione
esplicito invece di un mock a livello di libreria.

**Regola imparata a caro prezzo su questo branch:** un test la cui fixture lascia
che un'implementazione sbagliata produca comunque la risposta giusta è peggio di
nessun test — dà falsa sicurezza. Prima di considerare un nuovo test completo,
verificalo per mutazione: revert temporaneamente la riga di codice che dovrebbe
coprire e conferma che il test fallisce. Se resta verde, il test non sta
verificando quello che pensi. Vedi `docs/api-findings.md` per un caso reale in cui
proprio questo ha lasciato passare un bug per otto round di review.

## Dove l'API reale contraddice la sua documentazione

Non ripetuto qui: leggi `docs/api-findings.md` prima di toccare `hetzner.py` o
`compare.py`. Riguarda in particolare i campi di prezzo (stringa, non numero),
l'unità di `included_traffic`, e quale segnale di disponibilità/deprecazione è
affidabile e quale è stantio.

## Cosa un agente non deve fare

- Toccare, leggere o stampare `.env` (contiene token reali; è in `.gitignore`).
- Committare token o altri segreti, in `config.yaml`, nei test o altrove.
- Aggiungere dipendenze oltre a `httpx`, `pyyaml`, `pytest`.
- Scrivere codice che crea, ridimensiona, cancella o altrimenti muta una risorsa
  Hetzner — nessuna eccezione, nemmeno dietro un flag o per un caso d'uso futuro.
- Refactorare `docs/runbook-rescale.md`: è una procedura manuale verificata sul
  campo, si aggiorna solo con una nuova verifica reale, non per stile.
