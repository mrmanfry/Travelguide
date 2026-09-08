# Mappa delle chiavi e dei segreti

Dove vive ogni valore, chi lo legge, e cosa non deve mai succedere.

## Regola d'oro
**`GATE_SECRET` (Modal) e `MODAL_GATE_SECRET` (Lovable) devono essere lo stesso
identico valore.** È la causa numero uno dei guasti: se divergono, Modal risponde
403 e l'app dice "L'atelier è occupato".
Scrivi il segreto **una volta** in un posto tuo e copialo da lì in entrambi i
pannelli: non digitarlo due volte.

---

## 1. Anthropic
| Cosa | Valore | Dove va |
|---|---|---|
| API key (tua, con credito) | `sk-ant-...` | **Solo** nel secret Modal `anthropic-secret`, chiave `ANTHROPIC_API_KEY` |

Il motore legge `GUIDE_ENGINE_KEY`, ma `modal_app.py` fa da ponte e accetta anche
`ANTHROPIC_API_KEY`. Non deve stare **mai** nel frontend né su Lovable: è la
chiave che paga le generazioni.

## 2. Modal
| Cosa | Dove | Note |
|---|---|---|
| Token CLI (Token ID + Token Secret) | solo sulla macchina da cui si fa il deploy (Cloud Shell) | serve a `modal token set`, non all'app |
| Secret `anthropic-secret` → `ANTHROPIC_API_KEY` | dashboard Modal | la chiave Anthropic |
| Secret `anthropic-secret` → `GATE_SECRET` | dashboard Modal | il segreto della porta |

⚠️ **Modificare un secret non basta**: i container caldi tengono il vecchio
valore. Dopo ogni modifica serve `modal deploy modal_app.py`.
✅ **Verifica**: `curl .../health` deve rispondere `{"porta":"attiva"}`. Se dice
`DISATTIVATA`, `GATE_SECRET` non è arrivato e gli endpoint costosi sono aperti.

## 3. Cloudflare Turnstile
| Cosa | Dove va | Pubblico? |
|---|---|---|
| **Site key** (`0x4AAAAAAEmGQ5T6hgCmT_Ia`) | nel frontend Lovable | sì, può stare nel codice |
| **Secret key** | variabile server Lovable `TURNSTILE_SECRET_KEY` | no, mai nel frontend |

## 4. Lovable (variabili d'ambiente lato server)
La porta gira come *server function* dell'app (`src/lib/gate.functions.ts`),
quindi questi valori stanno nei segreti del **progetto Lovable**.

| Nome | Valore | Obbligatorio |
|---|---|---|
| `MODAL_BASE_URL` | `https://filippomanfroni--travelguide-web.modal.run` | sì |
| `MODAL_GATE_SECRET` | uguale a `GATE_SECRET` su Modal | sì |
| `TURNSTILE_SECRET_KEY` | Secret key di Cloudflare | sì |
| `EMAIL_ILLIMITATE` | email di prova separate da virgola, esenti dai limiti per email (non dal tetto di spesa) | no |

⚠️ Vanno impostate **sia per l'ambiente di sviluppo sia per quello pubblicato**:
se mancano solo in uno dei due, il sintomo è "Il servizio non è ancora
configurato del tutto" in quell'ambiente.
✅ **Diagnosi**: quando manca qualcosa, nei log del server compare
`configurazione mancante: NOME`.

## 5. Supabase
Gestito da Lovable (auth + database). URL e chiave anon stanno nel frontend
(sono pubbliche per progetto); la service role resta lato server. Non serve
toccarle a mano.

## 6. Stripe — quando arriverà
| Cosa | Dove va |
|---|---|
| Publishable key | frontend |
| Secret key | variabile server Lovable |
| Webhook signing secret | variabile server Lovable |

Iniziare in **modalità test**: nessun denaro vero finché il flusso non è provato.

---

## Cosa non deve mai finire nel frontend
La chiave Anthropic, `GATE_SECRET`/`MODAL_GATE_SECRET`, la Secret key di
Turnstile, la Secret key di Stripe, la service role di Supabase.
