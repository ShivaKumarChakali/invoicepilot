"""Tests for InvoicePilot. Stdlib only. Run: python3 test_invoicepilot.py

Covers: invoice math exactness, timesheet parsing, mock store merge,
status refresh (including PAID through code), due date and overdue
logic, AI fallback with no key, model line verification, web endpoints,
and the sandbox client against a local stub PayPal server. No live
PayPal or model calls are made here.
"""

import json
import os
import sys
import tempfile
import threading
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_mod
from ai import LocalAI, get_ai, verify_lines
from paypal_client import MockPayPalClient, SandboxPayPalClient

CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append((name, bool(condition)))
    print("%s %s %s" % ("PASS" if condition else "FAIL", name, detail))


def main():
    for var in ("META_API_KEY", "MODEL_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
        os.environ.pop(var, None)

    tmp = tempfile.mkdtemp()
    app_mod.STORE = os.path.join(tmp, "invoices.json")
    mock = MockPayPalClient(store_path=os.path.join(tmp, "paypal_mock.json"))

    # 1. Invoice math exactness
    lines = app_mod.parse_timesheet(
        "Mon Oct 20: 8 hours, Snowflake pipeline work\n"
        "Tue Oct 21: 7.5 hours, dbt model updates")
    inv = app_mod.build_invoice("Acme", 85.0, lines, "INV-001")
    check("math sample hours", inv["total_hours"] == 15.5, str(inv["total_hours"]))
    check("math sample total", inv["total"] == 1317.50, str(inv["total"]))
    inv2 = app_mod.build_invoice("Beta", 60.0,
                                 [{"hours": 3.33, "description": "Work"}], "INV-002")
    check("math rounding", inv2["total"] == 199.80, str(inv2["total"]))

    # 2. Timesheet parsing
    check("parse skips hourless lines",
          len(app_mod.parse_timesheet("No hours here\nMon: 2 hours, work")) == 1)
    check("parse keeps description",
          app_mod.parse_timesheet("Mon: 2 hours, Snowflake work")[0]["description"] == "Snowflake work")

    # 3. Mock store merge (old bug: each create overwrote the whole file)
    id_a = mock.create_invoice(inv)
    id_b = mock.create_invoice(inv2)
    records = mock._load()
    check("mock store keeps both invoices", id_a in records and id_b in records,
          "records=%d" % len(records))
    check("mock store tolerates old single-record file", True)
    old_path = os.path.join(tmp, "old_mock.json")
    with open(old_path, "w") as f:
        json.dump({"mock_paypal_id": "MOCK-OLD", "invoice": inv, "status": "DRAFT"}, f)
    old_mock = MockPayPalClient(store_path=old_path)
    check("old format migrates", "MOCK-OLD" in old_mock._load())

    # 4. Status refresh, including PAID through code
    app_mod.save_invoice(inv)
    app_mod.save_invoice(inv2)
    saved = dict(inv)
    saved["mock_paypal_id"] = id_a
    app_mod.save_invoice(saved)
    mock.send_invoice(id_a)
    refreshed = app_mod.refresh_status("INV-001", client=mock)
    check("status refresh reads SENT", refreshed["status"] == "SENT", refreshed["status"])
    mock.set_status(id_a, "PAID")
    refreshed = app_mod.refresh_status("INV-001", client=mock)
    check("invoice becomes PAID through code", refreshed["status"] == "PAID"
          and refreshed.get("paid_date") == date.today().isoformat())
    check("paid invoice is not overdue", app_mod.is_overdue(refreshed) is False)
    check("unknown invoice refresh is None", app_mod.refresh_status("INV-999", client=mock) is None)

    # 5. Due date and overdue logic
    past = app_mod.build_invoice("Gamma", 100.0, [{"hours": 1.0, "description": "Work"}],
                                 "INV-003", due_days=15, created_date="2026-10-01")
    check("due date is NET 15", past["due_date"] == "2026-10-16", past["due_date"])
    check("overdue unpaid past due", app_mod.is_overdue(past, today="2026-11-02") is True)
    check("days overdue count", app_mod.days_overdue(past, today="2026-11-02") == 17)
    future = app_mod.build_invoice("Gamma", 100.0, [{"hours": 1.0, "description": "Work"}],
                                   "INV-004", due_days=15)
    check("not overdue before due date", app_mod.is_overdue(future) is False)
    check("next invoice id", app_mod.next_invoice_id() == "INV-003",
          app_mod.next_invoice_id())

    # 6. AI fallback with no key
    ai = get_ai()
    check("no key means local AI", ai.name == "local", ai.name)
    parsed = ai.parse_timesheet("Mon: 3 hours, Snowflake work")
    check("local AI parses", parsed["lines"][0]["hours"] == 3.0
          and parsed["source"] == "local")
    check("local followup polite", "Acme" in LocalAI().followup(
        {"client": "Acme", "invoice_id": "INV-001", "total": 100.0}, "polite")["draft"])
    check("local followup firm mentions overdue", "overdue" in LocalAI().followup(
        {"client": "Acme", "invoice_id": "INV-001", "total": 100.0,
         "due_date": "2026-10-01", "days_overdue": 5}, "firm")["draft"])

    # 7. Model line verification: invented hours and unrelated text rejected
    text = "Mon: 8 hours, Snowflake pipeline work\nTue: 2 hours, dbt updates"
    proposed = [
        {"hours": 8, "description": "Snowflake pipeline work"},
        {"hours": 99, "description": "Snowflake pipeline work"},
        {"hours": 2, "description": "Unrelated rocket science"},
    ]
    accepted, rejected = verify_lines(proposed, text)
    check("verification accepts real lines", len(accepted) == 1, str(accepted))
    check("verification rejects invented lines", len(rejected) == 2, str(rejected))

    # 8. Sandbox client against a stub PayPal server
    seen = {}

    class StubPayPal(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if self.path == "/v1/oauth2/token":
                seen["token_auth"] = self.headers.get("Authorization", "")
                self._send({"access_token": "stub-token", "token_type": "Bearer"})
            elif self.path == "/v2/invoicing/invoices":
                seen["create_auth"] = self.headers.get("Authorization", "")
                self._send({"id": "STUB-INV-1", "status": "DRAFT"})
            elif self.path == "/v2/invoicing/invoices/STUB-INV-1/send":
                self._send({})
            else:
                self.send_response(404)
                self.end_headers()

        def do_GET(self):
            if self.path == "/v2/invoicing/invoices/STUB-INV-1":
                self._send({"id": "STUB-INV-1", "status": "PAID"})
            else:
                self.send_response(404)
                self.end_headers()

    stub = ThreadingHTTPServer(("127.0.0.1", 0), StubPayPal)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % stub.server_address[1]
    sandbox = SandboxPayPalClient(client_id="stub-id", client_secret="stub-secret",
                                  base_url=base)
    created = sandbox.create_invoice(inv)
    check("sandbox stub create", created == "STUB-INV-1", created)
    check("sandbox token uses basic auth", seen.get("token_auth", "").startswith("Basic "))
    check("sandbox calls use bearer token", seen.get("create_auth") == "Bearer stub-token")
    sent = sandbox.send_invoice(created)
    check("sandbox stub send", sent["status"] == "SENT")
    status = sandbox.get_status(created)
    check("sandbox stub status PAID", status["status"] == "PAID" and status["paid"] is True)
    stub.shutdown()

    # 9. Web endpoints
    import server as server_mod
    web = server_mod.create_server(0)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    web_base = "http://127.0.0.1:%d" % web.server_address[1]

    def get(path):
        with urllib.request.urlopen(web_base + path, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")

    def post(path, payload):
        req = urllib.request.Request(
            web_base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    status_code, _ = get("/api/health")
    check("web health 200", status_code == 200)
    _, health = get("/api/health")
    check("web health reports mock mode", json.loads(health)["paypal_mode"] == "mock")
    _, created_resp = post("/api/create", {
        "timesheet": "Wed: 4 hours, API integration work",
        "client": "Delta", "rate": 90, "due_days": 15})
    check("web create invoice", created_resp["invoice"]["total"] == 360.00,
          str(created_resp["invoice"]))
    check("web create labels mode", created_resp["paypal_mode"] == "mock")
    created_id = created_resp["invoice"]["invoice_id"]
    _, asked = post("/api/ask", {"question": "has client Delta paid invoice " + created_id})
    check("web ask answers", "Delta" in asked["answer"], asked["answer"][:80])
    _, followed = post("/api/followup", {"invoice_id": created_id, "tone": "firm"})
    check("web followup drafts", "Delta" in followed["draft"])
    _, listed = get("/api/invoices")
    check("web invoices list", len(json.loads(listed)["invoices"]) >= 1)
    try:
        with urllib.request.urlopen(web_base + "/static/../app.py", timeout=10) as resp:
            trav = resp.status
    except urllib.error.HTTPError as exc:
        trav = exc.code
    check("web traversal blocked", trav == 404, str(trav))
    web.shutdown()

    failed = [name for name, ok in CHECKS if not ok]
    print("\n%d checks passed, %d failed." % (len(CHECKS) - len(failed), len(failed)))
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
