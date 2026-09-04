# Stato del progetto — handoff

Documento di ripresa: leggilo per riprendere il lavoro in una sessione nuova.

## Cos'è
Motore che, da un brief di viaggio, scrive una guida-libro personalizzata
(capitoli narrativi, verificati con ricerca web, con critica e lista di
revisione). Attorno, un prototipo web: form → intervista AI → generazione →
lettura.

## Architettura
- `engine/` — motore Python (indipendente dall'host). Vedi README.
- `modal_app.py` — il motore esposto come servizio su **Modal**.
  Endpoint live: `https://filippomanfroni--travelguide-web.modal.run`
  (`/health`, `POST /intervista`, `POST /generate`, `GET /jobs/{id}`,
  `GET /jobs/{id}/guida.md`, `/da_rivedere.md`).
- Frontend **Lovable** (React): progetto `4a6b4fed-5e13-4cf0-ab64-4fddcd1d32a2`
  preview `https://id-preview--4a6b4fed-5e13-4cf0-ab64-4fddcd1d32a2.lovable.app`
  Flusso: Landing → Form brief → Intervista → Avanzamento → Guida.

Branch di lavoro: `claude/guida-grecia-ycoy3u` (PR #1 aperta verso `main`).

## Cosa funziona (provato end-to-end)
Form → intervista → `/generate` → avanzamento live per capitolo → arresto al
tetto di spesa. Guida intera della Grecia generata (14 capitoli, ~$17).

## Decisioni prese
- **Fixer spento** (`FIXER_ENABLED=False`): i capitoli con alert non risolti si
  consegnano e vanno in `da_rivedere.md`; la guida non si ferma.
- **Intervista**: voce "il vostro autore" — reagisce, un asse per domanda,
  opzioni tappabili, chiusura col ritratto.
- **Modello di business**: assaggio gratis (outline + intro + primo
  capitolo-tappa) → paywall → libro intero. Mai spendere i ~$17 prima
  dell'incasso.
- **Prezzo per dimensione del libro**, svelato dopo l'outline:
  €19 (5-7 cap) / €29 (8-12) / €39 (13-18) / €49 (19+).
- **Anti-abuso**: login magic link + Google (Supabase via Lovable) + Cloudflare
  Turnstile + **tetto di spesa globale €10/giorno** + limite per email + Modal
  chiuso con un segreto (solo la "porta" lo chiama).

## Fatto
- **Fase 1 — la porta.** Supabase auth (magic link + Google), Turnstile, tetto
  €10/giorno e limite per email nelle Edge Function; Modal accetta solo chiamate
  con l'header `X-Gate-Secret`.
- **Fase 2 — assaggio servito.** `anteprima=True` ferma il motore dopo
  l'introduzione e scrive `anteprima.md`; `/jobs` risponde fase `anteprima` con
  il link e con `libro: {capitoli, prezzo_eur}` per il paywall.

## Prossimi passi
1. **Fase 3 — pagamenti.** Stripe: paywall col prezzo che `/jobs` già espone →
   sblocco → generazione del libro intero (~45 min, async).
2. **Email di consegna** ("il vostro libro è pronto"): dopo il pagamento
   l'attesa è lunga, serve avvisare.
3. **Look di produzione** con Claude Design (traccia separata).
4. Poi: PDF del libro; avviso quando il credito Anthropic scende sotto soglia.

## Note operative
- Il deploy Modal **non si può fare da Claude Code**: l'egress proxy blocca
  `*.modal.com` e `*.modal.run` (403). Si fa da **Google Cloud Shell**:
  `cd ~/Travelguide && git pull && modal deploy modal_app.py`
- Test economico: campo `_tetto_usd` nel body di `/generate` (es. `0.30`)
  limita la spesa del run (outline + primo capitolo, ~$0.50).
- Il secret Anthropic su Modal si chiama `anthropic-secret` ed espone
  `ANTHROPIC_API_KEY` (il motore legge `GUIDE_ENGINE_KEY`, c'è un ponte).
- **Dopo ogni deploy**: `curl .../health` deve dire `{"porta":"attiva"}`. Se dice
  `DISATTIVATA` manca `GATE_SECRET` e gli endpoint costosi sono aperti.
- Modificare un secret su Modal **non basta**: i container caldi tengono il
  vecchio valore, serve un nuovo `modal deploy`.
- L'assaggio è la sola introduzione (`ANTEPRIMA_FINO_A_TAPPA=False`), ~$0.5 a
  utente: col tetto di €10/giorno regge una decina di assaggi. Rimettere `True`
  (assaggio fino al primo capitolo-tappa, ~$4) quando Stripe incassa e il tetto
  sale.
