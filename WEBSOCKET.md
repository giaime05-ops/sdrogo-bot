# WebSocket delle corse online

Riserva: il sistema HTTP (`/api/room/sync`) resta sempre attivo. Il WebSocket è un secondo canale per la stessa logica (`webrooms.sync_core`).

## Attivare (Northflank)
1. Servizio → **Networking** → *Add port*: porta `8081`, protocollo **HTTP**, *Publicly expose*. Annota l'indirizzo pubblico (es. `p01--sdrogobot--xxxx.code.run`).
2. **Environment** → aggiungi `WS_URL=wss://<quell'indirizzo>` (con `wss://`, senza porta né slash finale).
3. Riavvia/ricostruisci il servizio.
4. Su Telegram, da admin: `/ws stat` (deve dire "server attivo"), poi `/ws on`.

## Tornare indietro
- Subito: `/ws off` (chiude le connessioni, i client tornano a HTTP durante la corsa).
- Da deploy: variabile `WS_DISABLE=1` (il server WebSocket non parte), oppure togli `WS_URL`.
- Da codice: `git revert <commit>` dei commit "WebSocket".

## Variabili
`WS_URL` (indirizzo pubblico), `WS_PORT` (default 8081), `WS_DISABLE` (1 = spento del tutto).
