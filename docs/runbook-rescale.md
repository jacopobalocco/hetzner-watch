# Runbook — ridimensionare un server Hetzner Cloud via API

Procedura eseguita e verificata il 2026-08-08 su un server di produzione, passando da
`cpx12` (1 vCPU / 2 GB / 40 GB) a `cx33` (4 vCPU / 8 GB / 80 GB) nella stessa location.
Downtime misurato: **circa 90 secondi**.

Questo runbook documenta il caso "il job ha trovato un'alternativa migliore e voglio
prenderla". Non è automatizzato: ogni passo va deciso da una persona, per le ragioni
in *Prima di iniziare*.

## Prima di iniziare

Tre cose da sapere, tutte imparate sul campo:

**Serve un token Read & Write.** Il token che usa il job è read-only apposta, e non può
fare nulla di quanto segue. Creane uno read-write per l'operazione e **revocalo subito
dopo**: non lasciarlo in giro e non riusarlo per il job.

**Ingrandire il disco è irreversibile.** `upgrade_disk: true` è obbligatorio per passare
a un tipo con disco più grande, e da quel momento non si torna a un tipo con disco più
piccolo. Se il tipo di destinazione ha lo **stesso** disco dell'origine, l'operazione è
invece reversibile. Verifica quale dei due casi è il tuo prima di partire.

**Il server deve essere spento.** L'applicazione va offline per tutta la durata. Su un
servizio con utenti veri, scegli l'orario di conseguenza.

## Passo 0 — Verificare, senza toccare nulla

Prima di spegnere qualsiasi cosa, conferma tre fatti: quale server stai per toccare, che
il tipo di destinazione sia davvero disponibile nella tua location, e che non ci siano
protezioni o volumi esterni che complicano le cose.

```python
import os, httpx
c = httpx.Client(base_url="https://api.hetzner.cloud/v1",
                 headers={"Authorization": f"Bearer {os.environ['HCLOUD_RW_TOKEN']}"},
                 timeout=30)

for s in c.get("/servers").json()["servers"]:
    print(s["id"], s["name"], s["server_type"]["name"], s["status"],
          s["location"]["name"], s["protection"], s["volumes"])

types = c.get("/server_types", params={"per_page": 50}).json()["server_types"]
target = next(t for t in types if t["name"] == TARGET_TYPE)
entry = next(l for l in target["locations"] if l["name"] == LOCATION)
print(target["cores"], target["memory"], target["disk"], target["architecture"])
print("available:", entry["available"], "deprecation:", entry["deprecation"])
```

Controlla in particolare:

- **`architecture`** uguale a quella attuale. Passare da `x86` ad `arm` richiede immagini
  Docker multi-arch: non è un ridimensionamento, è una migrazione applicativa.
- **`available: true`** nella tua location — e leggilo da `server_types[].locations[]`,
  non da `datacenters[].server_types.available`, che è una fonte inaffidabile. Vedi
  `api-findings.md` punto 6 per l'evidenza.
- **`volumes: []`** e **`protection`** con `delete` e `rebuild` a `false`. Un volume
  esterno non viene toccato dal ridimensionamento, ma è bene sapere che c'è.

## Passo 1 — Spegnere

`shutdown` invia un ACPI, cioè uno spegnimento pulito che lascia all'OS il tempo di
chiudere i servizi. Preferiscilo sempre a `poweroff`, che è l'equivalente di staccare
la corrente e può corrompere un database a metà scrittura.

```python
c.post(f"/servers/{SID}/actions/shutdown").raise_for_status()

import time
for _ in range(40):
    time.sleep(5)
    if c.get(f"/servers/{SID}").json()["server"]["status"] == "off":
        break
else:
    raise SystemExit("ancora acceso dopo 200s: l'OS non ha risposto all'ACPI")
```

Nell'esecuzione reale il server si è spento in **15 secondi**. Se dopo qualche minuto è
ancora acceso, l'OS sta ignorando l'ACPI: entra in SSH e indaga invece di forzare.

## Passo 2 — Snapshot

Non saltarlo. È l'unica via di ritorno da un'operazione irreversibile, e su un disco
poco occupato costa pochi centesimi al mese: si paga sulla dimensione **reale**, non su
quella del disco. Nel caso reale, 40 GB di disco hanno prodotto uno snapshot da 2,75 GB,
cioè circa 0,03 €/mese.

Farlo a server spento garantisce che il filesystem sia coerente.

```python
r = c.post(f"/servers/{SID}/actions/create_image",
           json={"type": "snapshot", "description": "pre-rescale <origine>-><destinazione>"})
r.raise_for_status()
image_id, action_id = r.json()["image"]["id"], r.json()["action"]["id"]

while True:
    time.sleep(10)
    a = c.get(f"/actions/{action_id}").json()["action"]
    if a["status"] != "running":
        assert a["status"] == "success", a.get("error")
        break
```

Durata reale: **20 secondi**.

## Passo 3 — Ridimensionare

```python
r = c.post(f"/servers/{SID}/actions/change_type",
           json={"server_type": TARGET_TYPE, "upgrade_disk": True})
r.raise_for_status()
action_id = r.json()["action"]["id"]

while True:
    time.sleep(10)
    a = c.get(f"/actions/{action_id}").json()["action"]
    if a["status"] != "running":
        assert a["status"] == "success", a.get("error")
        break
```

`upgrade_disk: False` mantiene il disco invariato e lascia l'operazione reversibile; usalo
se il tipo di destinazione ha lo stesso disco.

Durata reale: **40 secondi**. Hetzner **riaccende il server da solo** al termine — non
serve un `poweron` esplicito, ma verificalo:

```python
s = c.get(f"/servers/{SID}").json()["server"]
print(s["server_type"]["name"], s["status"])
```

Se risultasse ancora spento: `c.post(f"/servers/{SID}/actions/poweron")`.

## Passo 4 — Verificare che il servizio sia tornato

L'API che dice `running` significa solo che la VM è accesa. Quello che conta è che
l'applicazione risponda.

```bash
for i in $(seq 6); do
  curl -s -o /dev/null -w "%{http_code}\n" --max-time 15 https://<dominio>/
  sleep 10
done
```

Se l'IP non è cambiato — ed è il caso di un ridimensionamento — il certificato TLS
esistente resta valido e non c'è nessuna riemissione da aspettare.

## Passo 5 — Estendere il filesystem

Hetzner ingrandisce il **volume**, ma la partizione dentro l'OS si estende da sola solo
se `growpart` gira al boot. Sulle immagini Ubuntu cloud di norma è così, ma va verificato:
un disco da 80 GB con una partizione ferma a 40 non dà nessun errore, si limita a
riempirsi prima del previsto.

```bash
ssh <utente>@<host> 'df -h /'
```

Se la partizione non è cresciuta:

```bash
sudo growpart /dev/sda 1
sudo resize2fs /dev/sda1
```

## Dopo

- **Revoca il token read-write.** Console → Security → API tokens.
- **Tieni lo snapshot** finché non sei sicuro che tutto funzioni, poi cancellalo se il
  costo ti dà fastidio. A pochi centesimi al mese, tenerlo è spesso la scelta giusta.
- **Aggiorna il baseline del job**: viene riletto dall'API a ogni esecuzione, quindi si
  aggiorna da solo. La prossima issue confronterà le alternative contro il nuovo tipo, e
  se non ne esistono più la issue aperta verrà chiusa da sola.

## Se qualcosa va storto

Ripristina lo snapshot con `rebuild`. Il tipo di destinazione deve avere un disco **almeno
grande quanto** quello dello snapshot, quindi un ripristino su un tipo più capiente
funziona sempre.

```python
c.post(f"/servers/{SID}/actions/rebuild", json={"image": image_id}).raise_for_status()
```

`rebuild` **cancella il disco** e lo riscrive dallo snapshot: tutto ciò che è cambiato dal
momento dello snapshot in poi è perso. È una via di ritorno, non un merge.
