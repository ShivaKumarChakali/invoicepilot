"""InvoicePilot: timesheet to invoice, PayPal send, payment tracking.

Usage:
  python3 app.py demo
  python3 app.py create --client Acme --rate 85 --timesheet "Mon Oct 20: 8 hours, Snowflake work"
  python3 app.py query "has client Acme paid invoice INV-001"
  python3 app.py status INV-001
  python3 app.py followup INV-001 [--tone polite|firm]

Invoice math is done in code, so totals are always exact. The AI step
(timesheet text to structured lines, follow up tone, payment answers)
proposes text only: code re-parses and recomputes every hour and total,
and rejects any model line whose hours do not match the source text.
With no META_API_KEY set, the local fallback runs and says so.
PayPal runs in mock mode unless PAYPAL_CLIENT_ID and
PAYPAL_CLIENT_SECRET are set, in which case the sandbox client is used
and every result labels the mode honestly.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai import get_ai, local_parse
from paypal_client import get_client

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "data", "invoices.json")
DEFAULT_DUE_DAYS = 15


def parse_timesheet(text):
    """Local regex parse. Kept for compatibility; ai.py owns the AI path."""
    return local_parse(text)


def build_invoice(client, rate_per_hour, lines, invoice_id="INV-001",
                  due_days=DEFAULT_DUE_DAYS, created_date=None):
    total_hours = round(sum(x["hours"] for x in lines), 2)
    total = round(total_hours * rate_per_hour, 2)
    created = created_date or date.today().isoformat()
    due = (date.fromisoformat(created) + timedelta(days=due_days)).isoformat()
    return {
        "invoice_id": invoice_id,
        "client": client,
        "rate_per_hour": rate_per_hour,
        "lines": lines,
        "total_hours": total_hours,
        "total": total,
        "currency": "USD",
        "status": "DRAFT",
        "created_date": created,
        "due_date": due,
        "due_days": due_days,
    }


def save_invoice(invoice):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    records = []
    if os.path.exists(STORE):
        with open(STORE) as f:
            records = json.load(f)
    records = [r for r in records if r["invoice_id"] != invoice["invoice_id"]]
    records.append(invoice)
    with open(STORE, "w") as f:
        json.dump(records, f, indent=2)
    return invoice


def load_invoices():
    if not os.path.exists(STORE):
        return []
    with open(STORE) as f:
        return json.load(f)


def find_invoice(invoice_id):
    for r in load_invoices():
        if r["invoice_id"].upper() == invoice_id.upper():
            return r
    return None


def days_overdue(invoice, today=None):
    """Days past due for an unpaid invoice, else 0."""
    if invoice.get("status") == "PAID" or not invoice.get("due_date"):
        return 0
    today = today or date.today().isoformat()
    delta = (date.fromisoformat(today) - date.fromisoformat(invoice["due_date"])).days
    return max(0, delta)


def is_overdue(invoice, today=None):
    return days_overdue(invoice, today) > 0


def with_overdue(invoice, today=None):
    invoice = dict(invoice)
    invoice["days_overdue"] = days_overdue(invoice, today)
    invoice["overdue"] = invoice["days_overdue"] > 0
    return invoice


def next_invoice_id():
    highest = 0
    for r in load_invoices():
        m = re.match(r"INV-(\d+)$", r["invoice_id"])
        if m:
            highest = max(highest, int(m.group(1)))
    return "INV-%03d" % (highest + 1)


def _local_payment_answer(question):
    records = load_invoices()
    m = re.search(r"invoice\s+([A-Za-z]+-?\d+)", question, re.I)
    client_m = re.search(r"client\s+([A-Za-z]+)", question, re.I)
    if m:
        inv_id = m.group(1).upper()
        for r in records:
            if r["invoice_id"].upper() == inv_id:
                paid = r["status"] == "PAID"
                return ("Invoice %s for %s: $%.2f, %s hours, status %s, due %s. Paid: %s."
                        % (r["invoice_id"], r["client"], r["total"], r["total_hours"],
                           r["status"], r.get("due_date", "unknown"), "yes" if paid else "no"))
        return "I have no record of invoice %s." % inv_id
    if client_m:
        name = client_m.group(1)
        found = [r for r in records if name.lower() in r["client"].lower()]
        if not found:
            return "I have no invoices for client %s." % name
        return "; ".join("%s $%.2f %s" % (r["invoice_id"], r["total"], r["status"]) for r in found)
    return "Ask me about an invoice id, for example: has client Acme paid invoice INV-001"


def answer_payment_query(question):
    """Answer from stored facts, via the AI when a key is set, else locally."""
    local = _local_payment_answer(question)
    ai = get_ai()
    try:
        result = ai.answer_question(question, load_invoices(), local)
        return result["answer"] + "\n[answered via %s]" % result["source"]
    except Exception as exc:
        return local + "\n[answered locally; AI call failed: %s]" % exc


def followup_draft(invoice_id, tone="polite"):
    invoice = find_invoice(invoice_id)
    if invoice is None:
        return "No invoice %s found." % invoice_id
    invoice = with_overdue(invoice)
    ai = get_ai()
    try:
        result = ai.followup(invoice, tone)
        draft = result["draft"]
        source = result["source"]
    except Exception as exc:
        from ai import _local_followup
        draft = _local_followup(invoice, tone)
        source = "local (AI call failed: %s)" % exc
    note = ""
    if invoice["overdue"]:
        note = "\n[Invoice is %d days overdue.]" % invoice["days_overdue"]
    elif invoice["status"] == "PAID":
        note = "\n[Note: this invoice is already paid.]"
    else:
        note = "\n[Note: this invoice is not due yet.]"
    return draft + note + "\n[drafted via %s]" % source


def refresh_status(invoice_id, client=None):
    """Refresh one invoice's status from the PayPal client and save it.

    The id looked up must belong to the current mode: a mock-mode record
    has no invoice on PayPal's sandbox, and a sandbox record has no mock
    entry. Mixing them used to send a mock id to PayPal and surface a
    raw 404, so a mismatch now fails with a plain explanation instead.
    """
    invoice = find_invoice(invoice_id)
    if invoice is None:
        return None
    client = client or get_client()
    if client.name == "sandbox":
        paypal_id = invoice.get("paypal_invoice_id")
        if not paypal_id:
            raise ValueError(
                "Invoice %s was created in mock mode and does not exist in "
                "the PayPal sandbox. Create a new invoice in sandbox mode "
                "to check a live status." % invoice_id)
    else:
        paypal_id = invoice.get("mock_paypal_id")
        if not paypal_id:
            raise ValueError(
                "Invoice %s was created in the PayPal sandbox and has no "
                "mock record. Set sandbox credentials to check its live "
                "status." % invoice_id)
    result = client.get_status(paypal_id)
    invoice["status"] = result["status"]
    if result.get("paid"):
        invoice["paid_date"] = date.today().isoformat()
    save_invoice(invoice)
    return with_overdue(invoice)


def create_invoice_from_timesheet(text, client_name, rate, due_days=DEFAULT_DUE_DAYS,
                                  invoice_id=None, recipient_email=None, paypal=None):
    """Full flow: AI parse (verified in code) -> invoice -> PayPal send."""
    ai = get_ai()
    try:
        parsed = ai.parse_timesheet(text)
        lines = parsed["lines"]
        ai_source = parsed["source"]
        rejected = parsed.get("rejected", [])
    except Exception as exc:
        parsed = {"lines": local_parse(text), "source": "local (AI call failed: %s)" % exc,
                  "rejected": []}
        lines = parsed["lines"]
        ai_source = parsed["source"]
        rejected = []
    if not lines:
        raise ValueError("No hours found in the timesheet text.")
    invoice = build_invoice(client_name, rate, lines,
                            invoice_id or next_invoice_id(), due_days)
    client = paypal or get_client()
    paypal_id = client.create_invoice(invoice, recipient_email=recipient_email)
    sent = client.send_invoice(paypal_id)
    invoice["status"] = sent["status"]
    if client.name == "mock":
        invoice["mock_paypal_id"] = paypal_id
    else:
        invoice["paypal_invoice_id"] = paypal_id
    invoice["paypal_mode"] = client.name
    save_invoice(invoice)
    return {"invoice": with_overdue(invoice), "paypal_id": paypal_id,
            "paypal_mode": client.name, "ai_source": ai_source,
            "rejected_lines": rejected}


def demo():
    text = "Mon Oct 20: 8 hours, Snowflake pipeline work\nTue Oct 21: 7.5 hours, dbt model updates"
    result = create_invoice_from_timesheet(text, "Acme", 85.0, invoice_id="INV-001")
    invoice = result["invoice"]
    print("Invoice %s: %s hours, $%.2f, PayPal %s (%s), status %s, due %s"
          % (invoice["invoice_id"], invoice["total_hours"], invoice["total"],
             result["paypal_id"], result["paypal_mode"], invoice["status"],
             invoice["due_date"]))
    print("Timesheet parsed via: %s" % result["ai_source"])
    print(answer_payment_query("has client Acme paid invoice INV-001"))
    print(followup_draft("INV-001"))
    print("PayPal mode: %s. AI source: %s."
          % (result["paypal_mode"], result["ai_source"]))


def main():
    p = argparse.ArgumentParser(description="InvoicePilot")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("demo")
    c = sub.add_parser("create")
    c.add_argument("--client", required=True)
    c.add_argument("--rate", type=float, required=True)
    c.add_argument("--timesheet", required=True)
    c.add_argument("--due-days", type=int, default=DEFAULT_DUE_DAYS)
    c.add_argument("--invoice-id")
    c.add_argument("--recipient-email")
    q = sub.add_parser("query")
    q.add_argument("question")
    s = sub.add_parser("status")
    s.add_argument("invoice_id")
    f = sub.add_parser("followup")
    f.add_argument("invoice_id")
    f.add_argument("--tone", default="polite", choices=["polite", "firm"])
    args = p.parse_args()
    if args.cmd == "demo":
        demo()
    elif args.cmd == "create":
        result = create_invoice_from_timesheet(
            args.timesheet, args.client, args.rate, args.due_days,
            args.invoice_id, args.recipient_email)
        inv = result["invoice"]
        print("Invoice %s: %s hours, $%.2f, PayPal %s (%s), status %s, due %s"
              % (inv["invoice_id"], inv["total_hours"], inv["total"],
                 result["paypal_id"], result["paypal_mode"], inv["status"], inv["due_date"]))
    elif args.cmd == "query":
        print(answer_payment_query(args.question))
    elif args.cmd == "status":
        invoice = refresh_status(args.invoice_id)
        if invoice is None:
            print("No invoice %s found." % args.invoice_id)
        else:
            print("Invoice %s: status %s, due %s, overdue: %s"
                  % (invoice["invoice_id"], invoice["status"],
                     invoice.get("due_date"), invoice["overdue"]))
    elif args.cmd == "followup":
        print(followup_draft(args.invoice_id, args.tone))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
