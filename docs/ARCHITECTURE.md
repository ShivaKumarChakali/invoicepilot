# InvoicePilot: Architecture

## Purpose

InvoicePilot is an AI agent for C2C and 1099 contractors. A contractor pastes a timesheet, the agent builds an invoice, sends it through PayPal, tracks whether it was paid, drafts a polite follow up for late payments, and answers questions like "has client Acme paid invoice INV-001".

## Tech stack

- Python 3, standard library only. No install needed, no pip packages.
- `argparse` for the command line, `json` for the store, `re` for parsing, `os` and `sys` for paths, `uuid` for mock PayPal ids.
- No web server, no database, no AI model wired in yet. The store is two JSON files in `data/`.

## Entry points

- `python3 app.py demo` : runs the full demo with a fixed sample timesheet.
- `python3 app.py query "has client Acme paid invoice INV-001"` : answers a payment question from the store.
- `python3 app.py followup INV-001` : prints a follow up draft for one invoice.
- `python3 app.py --help` : lists the three commands above.
- There is no `main()` call from another file. `app.py` is the only entry point. `paypal_client.py` is imported by `app.py` and is not run on its own.

## File map

- `app.py` (142 lines): CLI, timesheet parsing, invoice math, JSON store, payment query, follow up draft, demo.
- `paypal_client.py` (36 lines): mock PayPal client. Records what would be sent, returns a mock id and status. It does not call PayPal.
- `README.md`: what the project is, the honest mock note, timeline, prizes, how to run, next steps.
- `SUBMISSION_CHECKLIST.md`: PayPal hackathon judging rules, what to submit, dates, and status as of Oct 25, 2026.
- `data/invoices.json`: the invoice store. A list of invoice records. Created on first run, updated by `save_invoice`.
- `data/paypal_mock.json`: the last mock PayPal record only. Overwritten on every `create_invoice` call.
- `docs/ARCHITECTURE.md`: this file.

## Data flow, step by step

Demo flow, `python3 app.py demo`:
1. `demo()` in `app.py` uses a fixed two line timesheet: 8 hours of Snowflake pipeline work on Mon Oct 20, 7.5 hours of dbt model updates on Tue Oct 21.
2. `parse_timesheet(text)` finds the hours in each line with a regex, strips the day and date prefix, and returns a list of `{"hours": float, "description": str}`.
3. `build_invoice("Acme", 85.0, lines, "INV-001")` adds the hours (15.5), multiplies by the rate (85.0), and returns an invoice dict with `total` 1317.50, `currency` USD, and `status` DRAFT.
4. `MockPayPalClient().create_invoice(invoice)` makes a mock id like `MOCK-E7042F72`, writes the record to `data/paypal_mock.json`, and returns the id.
5. `MockPayPalClient().send_invoice(mock_id)` returns `{"status": "SENT"}`. No network call happens.
6. `demo()` sets `invoice["status"]` to SENT, adds `mock_paypal_id`, and calls `save_invoice(invoice)`, which loads `data/invoices.json`, replaces any record with the same `invoice_id`, appends the new one, and writes the file back.
7. `demo()` prints the invoice line, then the answer to "has client Acme paid invoice INV-001", then the follow up draft, then a note that PayPal is a mock.

Query flow, `python3 app.py query "..."`:
1. `answer_payment_query(question)` loads all invoices from `data/invoices.json`.
2. It looks for `invoice <id>` in the question first, with a regex. If it finds one, it returns the total, hours, status, and paid yes or no for that invoice, or "I have no record of invoice X".
3. If there is no invoice id, it looks for `client <name>` and lists every invoice for that client, or says it has none.
4. If neither matches, it tells the user to ask about an invoice id.

Follow up flow, `python3 app.py followup INV-001`:
1. `followup_draft(invoice_id)` loads the invoices, finds the matching id, and returns a fixed polite email draft with the client name, invoice id, and total.
2. If no invoice matches, it returns "No invoice X found".

## Key functions

All in `app.py` unless noted.

- `parse_timesheet(text)` : input is a multiline string, output is a list of `{"hours": float, "description": str}`. Lines with no hours are skipped. Description is the rest of the line after the day prefix and the hours are removed.
- `build_invoice(client, rate_per_hour, lines, invoice_id="INV-001")` : input is a client name, an hourly rate, and the parsed lines. Output is an invoice dict with `total_hours`, `total` (rounded to 2 places), `currency` USD, and `status` DRAFT. Math is done in code, never in a model.
- `save_invoice(invoice)` : input is an invoice dict. It reads `data/invoices.json`, replaces a record with the same `invoice_id`, appends the invoice, writes the file, and returns the invoice.
- `load_invoices()` : input is none. Output is a list of invoice dicts, or an empty list if the store file does not exist.
- `answer_payment_query(question)` : input is a plain English question string. Output is a plain English answer string. It matches invoice id first, then client name.
- `followup_draft(invoice_id)` : input is an invoice id. Output is a polite email draft string, or a "not found" string.
- `demo()` : input is none, it uses a fixed sample. Output is printed to the console: invoice line, payment answer, follow up draft, mock note.
- `main()` : parses the command line with `argparse` and calls `demo`, `answer_payment_query`, or `followup_draft`.
- `MockPayPalClient.create_invoice(invoice)` in `paypal_client.py` : input is an invoice dict. Output is a mock id string like `MOCK-XXXXXXXX`. Side effect: writes `data/paypal_mock.json` with the record and status DRAFT.
- `MockPayPalClient.send_invoice(mock_paypal_id)` : input is a mock id. Output is `{"mock_paypal_id": id, "status": "SENT"}`. No network call.
- `MockPayPalClient.get_status(mock_paypal_id)` : input is a mock id. Output is `{"mock_paypal_id": id, "status": "SENT", "paid": False}`. No network call. Note: `app.py` never calls this function today.

## How to run it exactly

From the repo folder:

```
python3 app.py demo
python3 app.py query "has client Acme paid invoice INV-001"
python3 app.py followup INV-001
python3 app.py --help
```

The demo was verified on Oct 27, 2026. It printed: invoice INV-001, 15.5 hours, $1317.50, mock PayPal id, status SENT, then the payment answer (Paid: no), then the follow up draft. Delete `data/invoices.json` to reset the store to empty. The sample invoice in the repo is INV-001 for Acme, 15.5 hours at $85.00, total $1317.50, status SENT.

## How to test it

There is no test file in this repo. That is a gap, see below. What was checked by hand:
- `python3 -m py_compile app.py paypal_client.py` passes for both files.
- `python3 app.py demo` exits 0 and prints the invoice, the payment answer, and the follow up draft.
- `python3 app.py query "has client Acme paid invoice INV-001"` returns the stored total and status.
- `python3 app.py followup INV-001` returns the draft for Acme.

## What is mock or missing

- PayPal is a mock. `paypal_client.py` records what would be sent and returns a mock id. It does not call PayPal, it does not use sandbox credentials, and there is no real create, send, or status call. Do not claim a real PayPal send until the sandbox wiring is done and tested.
- There is no AI step at all today. Timesheet parsing is a regex, the payment query is a regex, and the follow up is a fixed template. The hackathon requires meaningful use of PayPal and an AI tool, so the AI step is still to do.
- There is no web demo, no demo video, no license file, and no `.gitignore` in this folder yet. See `SUBMISSION_CHECKLIST.md` for the full list.

## Update, Nov 2, 2026: the gaps above are closed

The build that started from this scaffold is done, and the TODOs above
are resolved as follows:

- Real PayPal sandbox client: `paypal_client.py` now has
  `SandboxPayPalClient` (OAuth 2.0 client credentials, Invoicing API v2:
  create draft, send, get status), picked by `get_client()` when
  `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` are set. It is tested
  against a local stub PayPal server in `test_invoicepilot.py`. A live
  sandbox call still needs Shiva's sandbox credentials.
- AI step: `ai.py` adds a Meta Model API client (Muse Spark) plus a
  local fallback. Model lines are verified against the source text and
  totals are recomputed in code. Invoice math never comes from the model.
- Status path: `app.py status` refreshes the stored status from the
  client, so an invoice can become PAID through code. The mock store
  now merges by id instead of overwriting the file.
- Due dates: invoices carry `due_date` (NET 15 by default) and an
  overdue flag computed from status plus due date.
- Web demo: `server.py` plus `static/` serves the full flow on port
  8000. Static serving blocks path traversal, same as Pipeline Copilot.
- Tests: `test_invoicepilot.py`, 36 checks, all passing.
- License: MIT `LICENSE` at the top of the folder. `.gitignore` covers
  pycache, env files, and logs, and keeps the sample data.
- Still to do before submitting: live sandbox test with Shiva's
  credentials, and the demo video under 3 minutes on YouTube.

## Gaps and TODOs (original, Oct 26, kept for reference)

1. Real PayPal sandbox wiring. Replace the three methods in `MockPayPalClient` with real sandbox calls: create invoice, send invoice, get payment status. Read the Client ID and Secret from environment variables. Never put keys in the repo. This is the biggest gap.
2. Add the AI step. Use a model for timesheet text to structured lines and for follow up tone. Keep the invoice math in code, not in the model, so totals stay exact.
3. Fix the payment status path. `get_status` is never called, so no invoice ever becomes PAID through the code. Call it on query, or add a sync command, and store the paid date.
4. Fix the mock store. `create_invoice` overwrites `data/paypal_mock.json` on every call, so only the last invoice is kept there. Append to a list, or store one file per invoice.
5. Fix the relative path in `MockPayPalClient`. It defaults to `data/paypal_mock.json`, which only works when run from the repo folder. It swallows `FileNotFoundError` silently. Use an absolute path based on the file location, like `app.py` does for `STORE`, and fail loudly if the write fails.
6. Harden `parse_timesheet`. It drops lines with no hours, it has no date parsing, it does not validate the rate, and a corrupt `data/invoices.json` makes `load_invoices` crash with no clear message. Add date fields, validation, and a clear error when the store is corrupt.
7. Add tests. At minimum: parse a two line timesheet and check hours and total, build an invoice and check the total is exact, save and load round trip, query by invoice id and by client, follow up for a missing invoice. Run them with plain `python3` and no extra packages.
8. Add a due date and a late check. Today there is no due date on an invoice, so "late" has no meaning. Add `due_date`, and only draft a follow up when an invoice is past due and unpaid.
9. Make the follow up configurable. It hardcodes "Thanks, Shiva" and the client name. Add a sender name and a tone option.
10. Add a web demo, a license file, a `.gitignore`, and a demo video under 3 minutes before the Nov 12, 2026 deadline. Target draft Nov 5, final submit Nov 10.
