"""PayPal client for InvoicePilot.

Two implementations behind one interface:

    MockPayPalClient
        No credentials needed. Records invoices in a local JSON store and
        returns mock ids. This is the default, and it is what the app uses
        when no PayPal sandbox credentials are set.

    SandboxPayPalClient
        Calls the real PayPal sandbox API: OAuth 2.0 client credentials
        for an access token, then the Invoicing API v2 to create a draft
        invoice, send it, and read its status. Credentials come only from
        environment variables, never from code or data files.

Environment variables:
    PAYPAL_CLIENT_ID      sandbox app client id
    PAYPAL_CLIENT_SECRET  sandbox app secret
    PAYPAL_BASE_URL       override, default https://api-m.sandbox.paypal.com
    PAYPAL_INVOICER_EMAIL optional, used as the invoicer email on create

The client never logs credentials, and sends them only to the
configured PayPal base URL.

Both clients share one interface:
    create_invoice(invoice, recipient_email=None) -> paypal id string
    send_invoice(paypal_id) -> {"paypal_id": id, "status": str}
    get_status(paypal_id) -> {"paypal_id": id, "status": str, "paid": bool}

Invoice math stays in app.py, not here and not in the model.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


def get_client(store_path=None):
    """Return the sandbox client when credentials are set, else the mock."""
    client = SandboxPayPalClient()
    if client.enabled:
        return client
    return MockPayPalClient(store_path=store_path)


class MockPayPalClient:
    """Local stand-in for PayPal. No network calls, no money moves."""

    name = "mock"

    def __init__(self, store_path=None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.store_path = store_path or os.path.join(here, "data", "paypal_mock.json")

    def _load(self):
        """Load the store as {paypal_id: record}. Tolerates the old format."""
        if not os.path.exists(self.store_path):
            return {}
        with open(self.store_path) as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "records" in raw and isinstance(raw["records"], dict):
            return raw["records"]
        if isinstance(raw, dict) and "mock_paypal_id" in raw:
            return {raw["mock_paypal_id"]: raw}
        return {}

    def _save(self, records):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w") as f:
            json.dump({"records": records}, f, indent=2)

    def create_invoice(self, invoice, recipient_email=None):
        mock_id = "MOCK-" + uuid.uuid4().hex[:8].upper()
        records = self._load()
        records[mock_id] = {"mock_paypal_id": mock_id, "invoice": invoice, "status": "DRAFT"}
        self._save(records)
        return mock_id

    def send_invoice(self, mock_paypal_id):
        records = self._load()
        record = records.get(mock_paypal_id)
        if record is None:
            raise ValueError("Unknown mock PayPal id: %s" % mock_paypal_id)
        record["status"] = "SENT"
        self._save(records)
        return {"paypal_id": mock_paypal_id, "status": "SENT"}

    def get_status(self, mock_paypal_id):
        records = self._load()
        record = records.get(mock_paypal_id)
        if record is None:
            raise ValueError("Unknown mock PayPal id: %s" % mock_paypal_id)
        status = record["status"]
        return {"paypal_id": mock_paypal_id, "status": status, "paid": status == "PAID"}

    def set_status(self, mock_paypal_id, status):
        """Mock-only helper: simulate PayPal reporting a new status.

        Used by the demo and the tests to show an invoice becoming PAID.
        It has no real-world equivalent; the sandbox client reads status
        from PayPal instead.
        """
        records = self._load()
        record = records.get(mock_paypal_id)
        if record is None:
            raise ValueError("Unknown mock PayPal id: %s" % mock_paypal_id)
        record["status"] = status
        self._save(records)
        return {"paypal_id": mock_paypal_id, "status": status}


class SandboxPayPalClient:
    """Real PayPal sandbox client (Invoicing API v2)."""

    name = "sandbox"

    def __init__(self, client_id=None, client_secret=None, base_url=None):
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET")
        self.base_url = (base_url or os.environ.get("PAYPAL_BASE_URL")
                         or DEFAULT_SANDBOX_BASE_URL).rstrip("/")
        self._token = None

    @property
    def enabled(self):
        return bool(self.client_id and self.client_secret)

    def _access_token(self):
        if self._token:
            return self._token
        if not self.enabled:
            raise RuntimeError("PayPal sandbox credentials are not set. "
                               "Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.")
        credentials = base64.b64encode(
            ("%s:%s" % (self.client_id, self.client_secret)).encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            self.base_url + "/v1/oauth2/token",
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
            headers={
                "Authorization": "Basic " + credentials,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError("PayPal OAuth returned HTTP %d: %s" % (exc.code, detail))
        except urllib.error.URLError as exc:
            raise RuntimeError("Could not reach PayPal: %s" % exc.reason)
        self._token = payload["access_token"]
        return self._token

    def _request(self, method, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={
                "Authorization": "Bearer " + self._access_token(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and self._token:
                self._token = None
                return self._request(method, path, payload)
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError("PayPal API %s %s returned HTTP %d: %s"
                               % (method, path, exc.code, detail))
        except urllib.error.URLError as exc:
            raise RuntimeError("Could not reach PayPal: %s" % exc.reason)

    def _invoice_payload(self, invoice, recipient_email):
        items = [{
            "name": line["description"] or "Contract work",
            "quantity": str(line["hours"]),
            "unit_amount": {
                "currency_code": invoice.get("currency", "USD"),
                "value": "%.2f" % invoice["rate_per_hour"],
            },
        } for line in invoice["lines"]]
        payload = {
            "detail": {
                "invoice_number": invoice["invoice_id"],
                "currency_code": invoice.get("currency", "USD"),
            },
            "items": items,
        }
        invoicer_email = os.environ.get("PAYPAL_INVOICER_EMAIL")
        if invoicer_email:
            payload["invoicer"] = {"email_address": invoicer_email}
        if recipient_email:
            payload["primary_recipients"] = [
                {"billing_info": {"email_address": recipient_email}}]
        return payload

    def create_invoice(self, invoice, recipient_email=None):
        payload = self._invoice_payload(invoice, recipient_email)
        result = self._request("POST", "/v2/invoicing/invoices", payload)
        # Sandbox create returns 201 with a body that is only the self link,
        # e.g. {"rel": "self", "href": ".../invoices/INV2-...", "method": "GET"},
        # plus a Location header. The invoice id is the last path segment.
        if result.get("id"):
            return result["id"]
        href = result.get("href", "")
        if href:
            return href.rstrip("/").rsplit("/", 1)[-1]
        raise RuntimeError("PayPal create invoice returned no id: %r" % (result,))

    def send_invoice(self, paypal_id):
        self._request("POST", "/v2/invoicing/invoices/%s/send" % paypal_id, {})
        return {"paypal_id": paypal_id, "status": "SENT"}

    def get_status(self, paypal_id):
        result = self._request("GET", "/v2/invoicing/invoices/%s" % paypal_id)
        status = result.get("status", "UNKNOWN")
        return {"paypal_id": paypal_id, "status": status, "paid": status == "PAID"}
