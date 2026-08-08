# hetzner-watch — Design

**Data:** 2026-08-07
**Stato:** approvato, pronto per il piano di implementazione

## Obiettivo

Un job giornaliero che confronta il server Hetzner Cloud in uso contro il catalogo
Hetzner e segnala le alternative migliori sotto una soglia di spesa.

La domanda a cui risponde: *oggi esiste una macchina che costa meno della mia, o che a
parità di prezzo rende di più?*

## Non-obiettivi

Fuori scope, con la ragione per cui lo sono:

- **Server Auction (dedicati usati).** Il baseline è un CPX12 da ~5 €/mese; i dedicati
  all'asta partono da ~30-40 €/mese. Nessun confronto sensato è possibile finché il
  baseline resta di questa taglia. Riconsiderare solo se il baseline cresce.
- **Hetzner Robot (dedicati).** L'account non ne ha, e sono un'API separata con
  credenziali diverse.
- **Location extra-UE** (ash, hil, sin). Listino più caro e latenza peggiore per utenti
  italiani: segnalazioni che non verrebbero mai applicate.
- **Migrazione automatica.** Il job segnala, non agisce. Vedi *Sicurezza e costi*.

## Baseline

Scoperto a runtime, mai hardcoded: se il server viene ridimensionato, il confronto si
adegua da solo al run successivo.

`GET /v1/servers` restituisce per ogni server l'oggetto `server_type` completo (cores,
memory, disk, cpu_type, architecture) e il datacenter. Stato attuale dell'account:

| | |
|---|---|
| Server | `example-server-1` |
| Tipo | CPX12, x86 |
| Disco | 40 GB |
| Datacenter | fsn1 (Falkenstein) |
| Progetti | `progetto-esempio` (1 server), `Default` (vuoto) |

**I token hcloud sono per-progetto, non per-account.** Un token su `progetto-esempio` copre
l'unico server esistente. Il job legge però `HCLOUD_TOKEN` come **lista separata da
virgole** e itera su tutti i token: coprire anche il progetto `Default` in futuro
significa aggiungere un token al secret, non modificare il codice. I token stanno nei
secret, mai in `config.yaml`.

Se i token restituiscono più di un server, il job li tratta come baseline
indipendenti e produce una sezione di report per ciascuno.

## Catalogo

Due chiamate:

- `GET /v1/server_types` — specs e, per ogni location, prezzo orario/mensile in netto e
  lordo più il traffico incluso. **Paginare** (`per_page`): il default è 25 e il
  catalogo è più lungo.
- `GET /v1/datacenters` — per ogni datacenter, quali tipi sono `supported`,
  `available` e `available_for_migration`.

Il controllo su `available` è il pezzo che risponde letteralmente a *"sono venute
disponibili"*: i tagli si esauriscono e riappaiono, e un tipo a listino ma non
disponibile in fsn1 non è comprabile. Un candidato non disponibile non viene segnalato.

## Regola di confronto

Un tipo × location è un candidato se **tutte** queste condizioni valgono:

1. `price_monthly.net` ≤ **20,00 €** — vincolo rigido, soglia in **netto** (IVA esclusa),
   la stessa base in cui l'API espone i prezzi
2. location in `[fsn1, nbg1, hel1]`
3. `available: true` nel datacenter, e tipo non `deprecated`
4. **almeno una** metrica strettamente migliore del baseline

Le metriche confrontate: `cores`, `memory`, `disk`, `included_traffic`, `cpu_type`
(shared → dedicated conta come miglioramento) e `price_monthly.net`.

**Le regressioni non escludono un candidato.** Una scelta deliberata: la prima
esecuzione deve dare il quadro completo, non una lista già filtrata. Un tipo con +6 GB
di RAM e −20 GB di disco va visto, insieme al prezzo di quel trade-off.

Il report quindi mostra il **delta di ogni metrica con il segno**, miglioramenti e
peggioramenti allo stesso modo. `architecture` non è una metrica di qualità: è un flag
di compatibilità (vedi sotto).

### Conseguenza attesa

Con un baseline da 2 vCPU / 2 GB e un tetto a 20 € netti, quasi tutto il catalogo EU
migliora qualcosa. **La prima esecuzione produrrà ~10-20 righe.** È il comportamento
richiesto: un censimento una tantum, non un alert. La regola andrà stretta dopo aver
visto l'output reale — è un cambio di una condizione in `compare.py`.

### ARM

I tipi CAX (ARM) entrano nel confronto ma escono marcati **`richiede immagini
multi-arch`**. Il deploy Docker attuale è x86; una migrazione ad ARM comporta rebuild
delle immagini. Il marcatore tiene distinte le occasioni a costo zero da quelle che
costano lavoro, senza nascondere le seconde.

## Report e ciclo di vita della issue

Una sola issue aperta alla volta, identificata dalla label `hetzner-watch`.

| Situazione | Azione |
|---|---|
| Candidati presenti, nessuna issue aperta | Apri la issue |
| Candidati presenti, issue già aperta, insieme cambiato | Aggiorna il corpo |
| Candidati presenti, issue già aperta, nulla cambiato | Nessuna azione |
| Nessun candidato, issue aperta | Chiudi la issue con un commento |

Corpo della issue, in quest'ordine:

1. **Novità dall'ultimo run** — voci nuove, prezzi cambiati, candidati spariti. È la
   sezione che verrà effettivamente letta ogni giorno.
2. **Tabella completa** dei candidati con i delta firmati e il flag ARM.
3. **Baseline** usato per il confronto, e timestamp del run.

Nei giorni in cui l'insieme non cambia il job non scrive nulla e non notifica.

## Stato

`snapshot.json` committato dal job quando cambia. Contiene il baseline, i candidati
correnti (chiave `{tipo}@{location}`) con metriche e prezzo, e il timestamp.

Il diff rispetto allo snapshot precedente produce la sezione "novità". Come effetto
collaterale, la storia git del file diventa una **serie storica gratuita dei prezzi
Hetzner**.

Perché un file e non la issue come stato: con una manciata di candidati il corpo della
issue sarebbe bastato, ma con ~20 righe che cambiano prezzo e disponibilità nel tempo
il parsing di markdown come stato è fragile. Un JSON è robusto e diffabile.

## Struttura del repo

```
hetzner-watch/                    # repo privato
├── .github/workflows/daily.yml
├── src/hetzner_watch/
│   ├── hetzner.py     # client API: servers, server_types, datacenters
│   ├── compare.py     # baseline × catalogo → candidati + delta
│   ├── report.py      # candidati → markdown
│   ├── github.py      # apri / aggiorna / chiudi issue
│   └── __main__.py    # wiring
├── tests/fixtures/    # JSON API salvati
├── config.yaml
├── snapshot.json
└── pyproject.toml
```

Il confine che porta il peso è **`compare.py`**: prende due strutture dati e restituisce
candidati con i delta, senza toccare rete né GitHub. È l'unico modulo con logica non
banale, ed è per questo isolato — tutti i test che contano vivono lì e girano su
fixture.

Python gestito con `uv`.

## Config

```yaml
max_price_eur_net: 20.0
locations: [fsn1, nbg1, hel1]
include_arm: true
```

## Workflow

- Cron giornaliero alle **07:00 UTC**, più `workflow_dispatch` per l'esecuzione manuale.
- Permessi: `issues: write` (la issue), `contents: write` (il commit di `snapshot.json`).
- Secret: **`HCLOUD_TOKEN`** con permesso **Read**. Il token per le issue è il
  `GITHUB_TOKEN` fornito da Actions.

## Sicurezza e costi

**L'API Hetzner Cloud è gratuita** — si pagano le risorse create, non le chiamate. Il
rate limit è di ~3600 richieste/ora per progetto; il job ne fa 3-4 al giorno.

La garanzia vera non è però la gratuità delle chiamate, ma il **token read-only**: il
job non può creare, ridimensionare o cancellare nulla, quindi non può generare spesa
nemmeno in caso di bug.

GitHub Actions: repo privato su piano Free = 2000 minuti/mese inclusi, un run da ~30
secondi ne consuma ~15 al mese.

## Test

Fixture-driven, nessuna chiamata live.

| Cosa | Asserzione |
|---|---|
| `compare.py` — cap prezzo | Un tipo a 20,01 € netti è escluso; uno a 20,00 € incluso |
| `compare.py` — "almeno una migliore" | Un candidato con una metrica migliore e tre peggiori è **incluso** |
| `compare.py` — delta | Segno corretto su ogni metrica, incluse le regressioni |
| `compare.py` — disponibilità | Tipo a listino ma `available: false` nel datacenter → escluso |
| `compare.py` — deprecati | Tipo `deprecated` → escluso |
| `compare.py` — ARM | Tipo CAX presente e marcato `multi-arch` |
| diff snapshot | Dato uno snapshot precedente, "novità" contiene solo voci nuove o con prezzo cambiato |
| `hetzner.py` | Parsing corretto contro JSON reali salvati come fixture, paginazione inclusa |

## Da verificare in fase di implementazione

Assunzioni prese da documentazione e non ancora confermate contro l'API reale. Vanno
validate con una chiamata reale prima di scrivere `compare.py`:

- Nomi esatti dei campi in `server_types` (`price_monthly.net`, `included_traffic`,
  `cpu_type`, `architecture`, `deprecated`).
- Le specs reali di **CPX12** — lette dall'API, non assunte.
- Che `datacenters[].server_types.available` esprima davvero la disponibilità
  all'acquisto e non solo il supporto tecnico del datacenter.
- Se `included_traffic` sia per-location (dentro `prices[]`) o per-tipo.
