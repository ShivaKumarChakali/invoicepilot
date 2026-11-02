"""AI step for InvoicePilot.

Two implementations behind one interface:

    LocalAI
        No API key needed. Parses timesheets with the same regex the
        scaffold used, drafts follow ups from fixed templates in two
        tones, and answers payment questions from stored invoice facts.
        This is the default, and it is what the app uses today.

    MetaModelClient
        Calls the Meta Model API (Muse Spark) with an OpenAI compatible
        chat completions call. The model PROPOSES timesheet lines and
        follow up drafts, but it never owns the numbers: code re-parses
        and recomputes every hour and total, and rejects any model line
        whose hours do not appear in the source text or whose description
        shares no words with it. Invoice math always comes from code.

Environment variables:
    META_API_KEY            the Meta Model API key (also accepts MODEL_API_KEY)
    META_MODEL_API_BASE_URL override, default https://api.meta.ai/v1
    META_MODEL_API_MODEL    override, default muse-spark-1.1

The client never sends credentials anywhere except the configured API
base URL, and never logs the key. With no key set, get_ai() returns the
local implementation and every result says so in its output.
"""

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_MODEL_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.1"


def local_parse(text):
    """Regex parse of lines like 'Mon Oct 20: 8 hours, Snowflake work'."""
    lines = []
    for raw in text.strip().splitlines():
        m = re.search(r"(\d+(?:\.\d+)?)\s*hours?", raw, re.I)
        if not m:
            continue
        desc = raw.strip()
        # Strip a day/date prefix: "Mon Oct 20: " or a bare "Mon: ".
        desc = re.sub(r"^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+[^:]*:\s*",
                      "", desc, flags=re.I)
        desc = re.sub(r"^\s*\w+:\s*", "", desc)
        desc = re.sub(r"\d+(?:\.\d+)?\s*hours?,?\s*", "", desc, flags=re.I).strip(" ,")
        lines.append({"hours": float(m.group(1)), "description": desc or "Contract work"})
    return lines


def _hours_token_in_text(hours, text):
    """True when the hours number is written somewhere in the text."""
    forms = set()
    if hours == int(hours):
        forms.add(str(int(hours)))
        forms.add("%.1f" % hours)
    else:
        forms.add(("%g" % hours))
    for form in forms:
        if re.search(r"(?<![\d.])" + re.escape(form) + r"(?![\d.])", text):
            return True
    return False


def verify_lines(proposed, text):
    """Split model-proposed lines into accepted and rejected.

    A line is accepted only when its hours number appears in the source
    text and at least one word of its description appears in the text.
    Anything else is rejected, never silently kept.
    """
    accepted, rejected = [], []
    text_lower = text.lower()
    for line in proposed or []:
        hours = line.get("hours")
        desc = str(line.get("description") or "").strip()
        ok = (isinstance(hours, (int, float)) and not isinstance(hours, bool)
              and 0 < hours <= 24 and bool(desc)
              and _hours_token_in_text(float(hours), text))
        if ok:
            words = [w for w in re.findall(r"[a-z]+", desc.lower()) if len(w) > 2]
            ok = any(w in text_lower for w in words)
        if ok:
            accepted.append({"hours": float(hours), "description": desc})
        else:
            rejected.append(line)
    return accepted, rejected


def _parse_json_reply(text):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def _local_followup(invoice, tone):
    if tone == "firm":
        if invoice.get("days_overdue", 0) > 0:
            return (
                "Hi %s team, invoice %s for $%.2f was due on %s and is now %s days "
                "overdue. Please arrange payment this week, or let me know if "
                "there is a hold on your side I should know about. Thanks, Shiva"
                % (invoice["client"], invoice["invoice_id"], invoice["total"],
                   invoice.get("due_date", "the due date"),
                   invoice.get("days_overdue", 0))
            )
        return (
            "Hi %s team, invoice %s for $%.2f is due on %s. Please arrange "
            "payment by then, or let me know if there is a hold on your side "
            "I should know about. Thanks, Shiva"
            % (invoice["client"], invoice["invoice_id"], invoice["total"],
               invoice.get("due_date", "the due date"))
        )
    return (
        "Hi %s team, just checking in on invoice %s for $%.2f. "
        "Please let me know if you need anything else from me to process it. Thanks, Shiva"
        % (invoice["client"], invoice["invoice_id"], invoice["total"])
    )


class LocalAI:
    """No-key implementation: regex parse, template follow ups, fact answers."""

    name = "local"

    def parse_timesheet(self, text):
        return {"lines": local_parse(text), "source": "local",
                "rejected": [], "note": "No API key set, parsed locally."}

    def followup(self, invoice, tone="polite"):
        return {"draft": _local_followup(invoice, tone), "tone": tone, "source": "local"}

    def answer_question(self, question, invoices, local_answer):
        return {"answer": local_answer, "source": "local"}


class MetaModelClient:
    """Meta Model API client (OpenAI compatible chat completions)."""

    name = "meta-model-api"

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY")
        self.base_url = (base_url or os.environ.get("META_MODEL_API_BASE_URL")
                         or DEFAULT_MODEL_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("META_MODEL_API_MODEL") or DEFAULT_MODEL

    @property
    def enabled(self):
        return bool(self.api_key)

    def _chat(self, system, user):
        if not self.enabled:
            raise RuntimeError("Meta Model API key is not set. Set META_API_KEY.")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            # The model spends about 2,100 tokens reasoning before
            # answering, so a small limit returns an empty reply.
            "max_tokens": 4096,
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError("Meta Model API returned HTTP %d: %s" % (exc.code, detail))
        except urllib.error.URLError as exc:
            raise RuntimeError("Could not reach the Meta Model API: %s" % exc.reason)
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Unexpected response shape from the Meta Model API.")
        if not text:
            raise RuntimeError("Meta Model API returned an empty response.")
        return text

    def parse_timesheet(self, text):
        prompt = (
            "Convert this contractor timesheet to JSON with one object: "
            "{\"lines\": [{\"hours\": number, \"description\": string}]}. "
            "Use only hours numbers written in the text. Reply only with JSON.\n\n"
            + text
        )
        parsed = _parse_json_reply(self._chat(
            "You convert timesheets to JSON. Reply only with JSON.", prompt))
        accepted, rejected = verify_lines(parsed.get("lines"), text)
        if not accepted:
            fallback = local_parse(text)
            return {"lines": fallback, "source": "local (model lines rejected)",
                    "rejected": rejected,
                    "note": "The model lines failed verification, so the local parse was used."}
        return {"lines": accepted, "source": "meta-model-api", "model": self.model,
                "rejected": rejected,
                "note": "Model lines verified against the source text; totals recomputed in code."}

    def followup(self, invoice, tone="polite"):
        style = ("a polite nudge" if tone != "firm" else "a firm reminder")
        prompt = (
            "Write %s email to a client about an unpaid invoice. Facts: "
            "client %s, invoice %s, amount $%.2f, due date %s, %s days "
            "overdue, status %s. Keep it short, professional, and specific. "
            "Reply with the email text only."
            % (style, invoice["client"], invoice["invoice_id"], invoice["total"],
               invoice.get("due_date", "unknown"), invoice.get("days_overdue", 0),
               invoice.get("status", "UNKNOWN"))
        )
        draft = self._chat("You write short professional client emails.", prompt).strip()
        return {"draft": draft, "tone": tone, "source": "meta-model-api", "model": self.model}

    def answer_question(self, question, invoices, local_answer):
        facts = "; ".join(
            "%s for %s: $%.2f, %s hours, status %s, due %s"
            % (r["invoice_id"], r["client"], r["total"], r["total_hours"],
               r["status"], r.get("due_date", "unknown"))
            for r in invoices
        ) or "No invoices on record."
        prompt = (
            "Answer this question about contractor invoices in plain English, "
            "using only these facts. Facts: %s. Question: %s"
            % (facts, question)
        )
        text = self._chat("You answer invoice questions from given facts only.", prompt).strip()
        return {"answer": text + "\nVerified facts: " + local_answer,
                "source": "meta-model-api", "model": self.model}


def get_ai():
    """Return the Meta Model API client when a key is set, else the local AI."""
    client = MetaModelClient()
    if client.enabled:
        return client
    return LocalAI()
