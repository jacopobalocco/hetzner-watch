# Hetzner API — findings dalle fixture reali

Fonte: `tests/fixtures/{servers,server_types,datacenters}.json`, catturate il 2026-08-07
tramite `scripts/capture_fixtures.py` (progetto Hetzner dell'account, token Read-only).

1. **Campo prezzo netto mensile:** `prices[].price_monthly.net`, è una **stringa** (es.
   `"11.4900000000"`), non un numero. Va convertito a `Decimal`/`float` esplicitamente prima
   di qualunque confronto.

2. **Specs reali di CPX12** (`server_types[] where name == "cpx12"`, id `108`):
   `cores: 1`, `memory: 2` (GB), `disk: 40` (GB), `cpu_type: "shared"`,
   `architecture: "x86"`, `storage_type: "local"`, `category: "regular_purpose"`.

3. **`included_traffic`:** vive dentro `prices[]`, cioè è **per-location**, non un valore
   unico a livello di tipo — per CPX12 è `21990232555520` (20 TiB) su `fsn1`/`hel1`/`nbg1`
   ma solo `549755813888` (0.5 TiB) su `sin`. Unità: **byte** (i valori sono multipli esatti
   di 1024⁴).

4. **Flag di obsolescenza — esiste su due livelli, e i due livelli NON sono coerenti tra
   loro:**
   - **Type-level:** `server_types[].deprecated` (bool) + `server_types[].deprecation`
     (oggetto/null).
   - **Per-location:** `server_types[].locations[].deprecation` (oggetto/null; qui non
     esiste un bool `deprecated` separato, solo l'oggetto).

   **Forma osservata** dell'oggetto (uguale a entrambi i livelli, quando non null):
   `{"announced": "<ISO 8601>", "unavailable_after": "<ISO 8601>"}` — due chiavi.

   **Deprecati in questo snapshot:** nessun `server_type` ha `deprecated: true` a livello
   di tipo (0 su 25). Ma **5 tipi hanno `deprecation` non-null per-location**: `cpx11` (id
   22), `cpx21` (id 23), `cpx31` (id 24), `cpx41` (id 25), `cpx51` (id 26) — ciascuno
   deprecato su 4 delle sue 6 location (`fsn1`, `nbg1`, `hel1`, `sin`; non su `ash`/`hil`),
   tutti con `announced: "2025-10-16T06:00:00Z"` e `unavailable_after:
   "2025-12-31T23:59:59Z"`. Nelle location deprecate `locations[].available` è anche
   `false`; su `ash`/`hil` resta `true`.

   **Punto 4 — il bool type-level NON è affidabile da solo:** tutti e 5 questi tipi hanno
   `deprecated: false` a livello di tipo pur avendo `deprecation` non-null su 4 location su
   6. Un tipo può quindi essere "non deprecato" nel campo aggregato e comunque deprecato
   nella location specifica che interessa. **Conclusione per Task 3: il filtro deve essere
   per-location** (`server_types[].locations[].deprecation` per la location del server in
   esame), non il solo bool type-level — altrimenti questi 5 tipi verrebbero proposti come
   alternative valide in location dove sono in realtà in dismissione.

5. **`datacenters[].server_types.available` vs `.supported`:** **non coincidono** — il
   campo discrimina. Su 6 datacenter:
   - `nbg1-dc3`: available 16 / supported 24
   - `hel1-dc2`: available 14 / supported 24
   - `fsn1-dc14`: available 0 / supported 24 (nessun tipo attualmente disponibile)
   - `ash-dc1`: available 11 / supported 11 (coincidono)
   - `hil-dc1`: available 11 / supported 11 (coincidono)
   - `sin-dc1`: available 12 / supported 17

   Solo 2 dei 6 datacenter hanno i due insiemi uguali; negli altri 4 `supported` include
   sistematicamente più tipi di `available`. **Gate superato:** il campo non è ridondante,
   Task 3 può fare affidamento su `available` per l'availability reale.

6. **Correzione al punto 5 — `datacenters[].server_types.available` è stantio, non
   affidabile.** Il gate del punto 5 concludeva che Task 3 potesse fare affidamento
   su questo campo. **Falso**, scoperto durante la revisione finale del branch:

   - **`fsn1-dc14` dice 0 tipi disponibili su 24.** Ma il proprietario ha verificato
     in console (2026-08-07): CX23, CX33 e CX43 sono acquistabili ora a Falkenstein,
     con "Create & Buy now" attivo, a prezzi **lordi** 6,70 / 10,36 / 19,51 € — che
     sono esattamente i prezzi **netti** di fixture (5,49 / 8,49 / 15,99 €) più il
     22% di IVA (`5.49 * 1.22 = 6.6978`, `8.49 * 1.22 = 10.3578`,
     `15.99 * 1.22 = 19.5078`), e coincidono col campo `price_monthly.gross` già
     presente nella fixture per quei tre tipi in `fsn1`.
   - **`available ⊄ supported`:** cpx12 (id 108) compare nella lista `available` di
     `nbg1-dc3` e `hel1-dc2` (non in quella di `fsn1-dc14`, che è vuota) ma non
     compare in **nessuna** delle tre liste `supported` corrispondenti. Un campo
     "disponibile" che elenca id assenti dal campo "supportato" è già di per sé un
     segnale che il primo non è una fonte pulita.
   - **Il segnale corretto è per-tipo/per-location**, lo stesso già usato per
     `deprecation` al punto 4: `server_types[].locations[].available`. Per cpx12
     questo campo dice `true` su `fsn1`, `nbg1`, `hel1`, `sin` — coerente con la
     verifica in console. Idem per cx23/cx33/cx43 su `fsn1`.

   **Conclusione:** `server_types[].locations[].available` è la fonte autorevole
   di disponibilità; `datacenters[].server_types.available`/`.supported` sono
   stantii e non vanno più usati per questo scopo. `parse_offers` ora legge
   `available` da lì, con la stessa funzione di lookup già usata per
   `deprecation` (`_is_location_available`, specchio di `_is_location_deprecated`).
   Il payload `datacenters` resta catturato dallo script di fixture per eventuale
   diagnostica futura, ma non alimenta più il parsing.

## Nota collaterale (script di ispezione del brief)

Il comando di ispezione nel brief assume `servers[].datacenter.name` e
`servers[].datacenter.location.name`. Sull'endpoint reale `/v1/servers` questo campo non
esiste: il server espone direttamente `servers[].location` (oggetto con `name`, `city`,
`country`, `network_zone`, ecc.), non un campo `datacenter` annidato. L'ispezione qui sopra
è stata adattata di conseguenza; nessun impatto sulle fixture catturate.
