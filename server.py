"""InvoicePilot web demo.

Uses only the Python standard library (http.server). No pip installs.

Run it with:
    python3 server.py
Then open:
    http://localhost:8000

Set a different port with the PORT environment variable if needed.

What it serves:
    GET  /                  the web app (static/index.html)
    GET  /static/...        CSS and JavaScript
    GET  /api/health        status, PayPal mode, AI source, invoice count
    GET  /api/invoices      all stored invoices, with overdue flags
    POST /api/create        JSON in: {"timesheet": "...", "client": "...",
                             "rate": 85, "due_days": 15,
                             "recipient_email": "..."}
                            JSON out: the invoice, PayPal result, AI source
    POST /api/status        JSON in: {"invoice_id": "..."}
                            JSON out: the refreshed invoice
    POST /api/ask           JSON in: {"question": "..."}
                            JSON out: the answer and its source
    POST /api/followup      JSON in: {"invoice_id": "...", "tone": "polite"}
                            JSON out: the draft and its source

Every response labels the PayPal mode (mock or sandbox) and the AI
source (local or meta-model-api) honestly, so the demo never claims a
real send or a model answer it did not make.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import app as app_mod
from ai import get_ai
from paypal_client import get_client

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

CONTENT_TYPES = {".html": "text/html", ".css": "text/css",
                 ".js": "application/javascript", ".json": "application/json"}


def create_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        if self.path == "/api/health":
            client = get_client()
            ai = get_ai()
            return self.send_json({"status": "ok", "paypal_mode": client.name,
                                   "ai_source": ai.name,
                                   "invoices": len(app_mod.load_invoices())})
        if self.path == "/api/invoices":
            return self.send_json({"invoices": [app_mod.with_overdue(r)
                                                for r in app_mod.load_invoices()]})
        path = urlparse(self.path).path
        path = unquote(path)
        if path in ("/", "/index.html"):
            file_path = os.path.join(STATIC_DIR, "index.html")
        else:
            if not path.startswith("/static/"):
                return self.send_json({"error": "not found"}, status=404)
            rel_path = path[len("/static/"):]
            # Block path traversal: resolve the path and check it stays
            # inside the static folder.
            file_path = os.path.realpath(os.path.join(STATIC_DIR, rel_path))
            if os.path.commonpath([STATIC_DIR, file_path]) != STATIC_DIR:
                return self.send_json({"error": "not found"}, status=404)
        if not os.path.isfile(file_path):
            return self.send_json({"error": "not found"}, status=404)
        ext = os.path.splitext(file_path)[1]
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "text/plain"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = self.read_json()
        if payload is None:
            return self.send_json({"error": "request body must be JSON"}, status=400)
        if self.path == "/api/create":
            try:
                result = app_mod.create_invoice_from_timesheet(
                    payload.get("timesheet", ""), payload.get("client", "Client"),
                    float(payload.get("rate", 0)), int(payload.get("due_days", 15)),
                    payload.get("invoice_id"), payload.get("recipient_email"))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self.send_json({"error": "create failed: %s" % exc}, status=500)
            return self.send_json(result)
        if self.path == "/api/status":
            try:
                invoice = app_mod.refresh_status(payload.get("invoice_id", ""))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self.send_json({"error": "status check failed: %s" % exc}, status=500)
            if invoice is None:
                return self.send_json({"error": "invoice not found"}, status=404)
            return self.send_json({"invoice": invoice})
        if self.path == "/api/ask":
            try:
                answer = app_mod.answer_payment_query(payload.get("question", ""))
            except Exception as exc:
                return self.send_json({"error": "question failed: %s" % exc}, status=500)
            return self.send_json({"answer": answer})
        if self.path == "/api/followup":
            try:
                draft = app_mod.followup_draft(payload.get("invoice_id", ""),
                                               payload.get("tone", "polite"))
            except Exception as exc:
                return self.send_json({"error": "follow-up failed: %s" % exc}, status=500)
            return self.send_json({"draft": draft})
        return self.send_json({"error": "not found"}, status=404)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = create_server(port)
    client = get_client()
    ai = get_ai()
    print("InvoicePilot running at http://localhost:%d" % port)
    print("PayPal mode: %s" % client.name)
    print("AI source: %s" % ai.name)
    if client.name == "mock":
        print("No PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET set, so PayPal is a mock.")
        print("Set them to use the real PayPal sandbox. See README.md.")
    if ai.name == "local":
        print("No META_API_KEY set, so the AI step uses the local fallback.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
