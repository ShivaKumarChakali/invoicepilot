# InvoicePilot for Contractors

PayPal AI Hackathon entry. An AI agent for C2C and 1099 contractors:
paste a timesheet, and it builds the invoice, sends it through PayPal,
tracks whether it was paid, drafts follow ups for late payments, and
answers "has client X paid invoice Y" in plain English.

Data is stored as real invoice and payment records, not chat memory.

## What it does

1. You paste a timesheet in plain text, for example "Mon Nov 2: 8 hours,
   Snowflake pipeline work".
2. The AI step turns the text into structured lines. The model proposes
   lines, but code verifies every line against the source text and
   recomputes all hours and totals, so the numbers never come from the
   model.
3. The invoice is created with a due date (NET 15 by default, or the
   terms you set) and sent through PayPal: the real sandbox API when
   credentials are set, a clearly labeled mock otherwise.
4. A status check refreshes the stored status from PayPal, so an invoice
   can become PAID through code. Overdue is computed from status plus
   due date.
5. Follow ups come in two tones, polite nudge and firm reminder, and
   payment questions are answered from the stored facts.

## The honest status

What is real today:
- Invoice model, timesheet parsing, local JSON store, due dates,
  overdue flags, status refresh, follow up drafts, payment questions.
- A real PayPal sandbox client (OAuth 2.0 client credentials, then
  Invoicing API v2: create draft, send, get status). It is built and
  tested against a local stub server that mimics the PayPal endpoints,
  and verified live against the PayPal sandbox on Nov 2, 2026: a real
  sandbox invoice was created and sent (status SENT, confirmed by a
  status refresh from PayPal). Until credentials are set, the app runs
  in mock mode and says so on every result.
- A real AI step (Meta Model API, Muse Spark) with the same guardrails
  as the model layer in Pipeline Copilot: model proposes, code verifies
  and recomputes. With no key set, the local fallback runs and says so.

What is still to do before submitting:
- Demo video under 3 minutes on YouTube (required by the PayPal rules).
- Devpost submit by Nov 10 (hard deadline Nov 12,
  12:00 PM PT). Judging runs Dec 1 to Dec 15, so the demo must keep
  working until then.

No real money moves in mock mode, and no credentials are stored in this
repo. Ever.

## How to run: command line

You only need Python 3. Nothing to install.

```bash
python3 app.py demo
python3 app.py create --client Acme --rate 85 \
  --timesheet "Mon Nov 2: 8 hours, Snowflake pipeline work"
python3 app.py query "has client Acme paid invoice INV-001"
python3 app.py status INV-001
python3 app.py followup INV-001 --tone firm
```

The demo creates the sample invoice INV-001: 15.5 hours at $85 an hour,
$1,317.50, due NET 15, then prints the payment answer and a follow up
draft.

## How to run: web demo

```bash
python3 server.py
```

Then open `http://localhost:8000`. The page labels the PayPal mode
(mock or sandbox) and the AI source (local or meta-model-api) at the
top, so a mock send or a local fallback is never presented as a real
one. Set a different port with `PORT=9000 python3 server.py`.

API: `GET /api/health`, `GET /api/invoices`, `POST /api/create`,
`POST /api/status`, `POST /api/ask`, `POST /api/followup`.

## Environment variables

```bash
# Real PayPal sandbox (get these from a PayPal Developer sandbox app)
export PAYPAL_CLIENT_ID="your sandbox client id"
export PAYPAL_CLIENT_SECRET="your sandbox client secret"
# Optional: export PAYPAL_BASE_URL="https://api-m.sandbox.paypal.com"
# Optional: export PAYPAL_INVOICER_EMAIL="you@example.com"

# Real AI step (Meta Model API)
export META_API_KEY="your Meta Model API key"
# Optional: export META_MODEL_API_BASE_URL="https://api.meta.ai/v1"
# Optional: export META_MODEL_API_MODEL="muse-spark-1.1"
```

With neither set, the app runs fully in mock plus local mode, which is
enough to demo every feature except a real PayPal send and a model
draft.

## File map

- `app.py`: CLI, invoice math, store, due dates, status refresh, query,
  follow up, demo, create.
- `ai.py`: AI step. Local fallback plus Meta Model API client, with
  model line verification and code-recomputed totals.
- `paypal_client.py`: mock client plus real sandbox client, and the
  factory that picks one based on credentials.
- `server.py` and `static/`: web demo. Standard library only.
- `test_invoicepilot.py`: tests. Run with `python3 test_invoicepilot.py`.
- `data/invoices.json`: invoice store, with the INV-001 sample.
- `data/paypal_mock.json`: mock PayPal store, merged by invoice id.
- `docs/ARCHITECTURE.md`: purpose, data flow, every key function.

## Tests

```bash
python3 test_invoicepilot.py
```

36 checks: invoice math exactness, timesheet parsing, mock store merge,
status refresh including PAID through code, due date and overdue logic,
AI fallback with no key, model line verification, web endpoints, the
sandbox client against a stub PayPal server, and the static path
traversal block.

## Why this idea

Contractors bill by the hour and chase payments by hand. A timesheet
becomes an invoice late, the invoice goes out late, and the follow up
never gets written. InvoicePilot closes that loop: timesheet in, invoice
out through PayPal, payment tracked, follow up drafted when it is
overdue. It is built for the exact problem its builder lives with.

## License

MIT. See `LICENSE`. Copyright (c) 2026 Shiva Kumar Chakali.
