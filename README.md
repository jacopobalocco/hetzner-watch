# hetzner-watch

Job orario che confronta un server Hetzner Cloud in uso contro il catalogo
Hetzner e segnala, aprendo/aggiornando/chiudendo una issue GitHub, le alternative
che costano meno o rendono di più entro una soglia di spesa configurabile.

## Cosa fa e cosa non fa

1. Legge il server attivo dall'API Hetzner (token **Read-only**): nessuna spec
   hardcoded, il confronto si aggiorna da solo se ridimensioni la macchina.
2. Scarica il catalogo Hetzner e la disponibilità reale per location.
3. Segnala i tipi sotto soglia che migliorano almeno una metrica rispetto al tuo
   server, mostrando anche le eventuali regressioni con il segno.
4. Apre, aggiorna o chiude una issue con label `hetzner-watch`.
5. Avvisa quando il tipo `watch_type` torna ordinabile in una delle `locations`:
   apre una issue nuova per quella location (label `disponibile:<tipo>@<location>`),
   assegnata al proprietario del repo, e la chiude quando il tipo torna esaurito.
   Una issue aperta vale "ancora disponibile", quindi nessun avviso ripetuto;
   un ritorno dopo l'esaurimento apre una issue nuova. L'assegnazione serve
   perché una issue nuova notifica solo chi osserva il repo, e modificare il
   corpo di una issue non notifica nessuno.

Oggi il job monitora **solo il primo server trovato** tra tutti quelli restituiti
dai token configurati, anche se questi coprono più progetti o più server: gli
altri vengono ignorati (nessuna sezione di report dedicata, per ora). Se capita,
un avviso compare nei log del run.

**Cosa non fa, deliberatamente:** hetzner-watch è solo *decision support*. Non
ridimensiona, non crea, non cancella e non modifica alcuna risorsa Hetzner — mai.
Il token che usa ha permesso **Read soltanto**: anche in presenza di un bug non
può generare spesa né toccare la tua infrastruttura. Se decidi di agire su una
segnalazione, la procedura manuale è documentata in
[`docs/runbook-rescale.md`](docs/runbook-rescale.md); il job non la esegue.

## Prerequisiti

- Un account Hetzner Cloud con almeno un server attivo.
- Un repository GitHub (va bene anche privato) dove far girare il workflow e
  aprire le issue.
- [`uv`](https://docs.astral.sh/uv/) per gestire l'ambiente Python 3.12.

## Setup

1. **Crea un token API Hetzner con permesso Read soltanto**: Cloud Console del
   progetto → *Security* → *API tokens* → *Generate API token* → *Read*. Un
   token è legato a un singolo progetto Hetzner, non all'account: per monitorare
   più progetti genera un token per ciascuno.
2. **Salvalo come secret del repository GitHub**: *Settings* → *Secrets and
   variables* → *Actions* → *New repository secret*, nome `HCLOUD_TOKEN`. Per
   più progetti, metti tutti i token nello stesso secret separati da virgola.
3. **Il workflow** (`.github/workflows/daily.yml`) gira ogni ora al minuto
   17 (cron; GitHub può ritardarlo o saltarne qualcuno), più `workflow_dispatch` per lanciarlo a mano da *Actions →
   hetzner-watch → Run workflow*. Ad ogni run: installa le dipendenze, esegue i
   test, confronta il catalogo, apre/aggiorna/chiude la issue e — se l'insieme
   dei candidati è cambiato — committa `snapshot.json` sul branch. Per questo
   il workflow dichiara i permessi `issues: write` e `contents: write`.
4. Il token per l'API delle Issues (`GITHUB_TOKEN`) è fornito automaticamente
   da GitHub Actions: non c'è nulla da configurare per quello.

## Uso in locale

```bash
uv sync
cp .env.example .env      # compila HCLOUD_TOKEN, GITHUB_REPOSITORY, GITHUB_TOKEN
set -a; source .env; set +a
uv run python -m hetzner_watch
```

Il dettaglio di ogni variabile — cosa serve, dove trovarla, se è obbligatoria
solo in locale — è in [`.env.example`](.env.example).

## Test

```bash
uv sync
uv run pytest
```

94 test, fixture-driven: nessuna chiamata di rete durante i test e nessun
framework di mocking, solo risposte reali dell'API salvate come JSON in
`tests/fixtures/`. Per rigenerare le fixture dalla tua API reale:

```bash
export HCLOUD_TOKEN=...
uv run python scripts/capture_fixtures.py
```

## Config

`config.yaml`:

| Chiave | Significato |
|---|---|
| `max_price_eur_net` | Tetto di spesa in € **netti**/mese (IVA esclusa) |
| `locations` | Location Hetzner ammesse nel confronto |
| `include_arm` | Se includere i tipi ARM (CAX), marcati `multi-arch` |
| `watch_type` | Tipo di cui avvisare il ritorno in disponibilità, nelle `locations` |

Due cose che potresti osservare in output e che non sono un bug:

- **Zero disponibilità in una location.** La disponibilità reale dei tipi
  Hetzner cambia nel tempo e per location; se in un run la location del tuo
  server non ha nessun tipo disponibile, la issue lo segnala esplicitamente
  invece di mostrare semplicemente una tabella vuota. Vedi
  [`docs/api-findings.md`](docs/api-findings.md) per come viene letto questo
  segnale.
- **Nessun candidato ARM.** I tipi CAX possono risultare non disponibili in
  nessuna delle location configurate in un dato momento: se `include_arm: true`
  non produce righe ARM, verifica prima lo stato di disponibilità reale (in
  console o via API) invece di sospettare il filtro.

## Come leggere la issue

Una sola issue aperta alla volta, label `hetzner-watch`, in tre sezioni:

1. **Novità dall'ultimo run** — la parte da leggere ogni giorno: candidati
   nuovi, prezzi cambiati, candidati spariti, baseline cambiato (se hai
   ridimensionato il server), oppure "Nessuna variazione."
2. **Tutti i candidati** — tabella completa con i delta firmati (+/-) su ogni
   metrica rispetto al tuo server attuale, incluse le regressioni: un tipo con
   più RAM e meno disco compare comunque, con il trade-off visibile.
3. **Baseline** — il server e le sue specifiche usate per il confronto in quel
   run, più il timestamp.

Se nessun candidato è più sotto soglia, la issue aperta viene chiusa con un
commento automatico. Nei giorni in cui l'insieme dei candidati non cambia, il
job non scrive nulla: il campo `generated_at` in `snapshot.json` indica quando
l'insieme dei candidati è cambiato l'ultima volta, **non** quando il job è
stato eseguito l'ultima volta.

## Approfondimenti

- [`docs/api-findings.md`](docs/api-findings.md) — come si comporta davvero
  l'API Hetzner Cloud rispetto a quanto scritto nella sua documentazione
  ufficiale (campi che sono stringhe e non numeri, segnali di disponibilità
  stantii vs per-location, ecc.) e dove quella documentazione è incompleta o
  fuorviante.
- [`docs/runbook-rescale.md`](docs/runbook-rescale.md) — la procedura manuale,
  passo per passo, per agire su una segnalazione e ridimensionare davvero un
  server. hetzner-watch segnala; decidere e agire resta sempre una scelta
  umana.
