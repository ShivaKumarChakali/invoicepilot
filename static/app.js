async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  return res.json();
}

async function loadHealth() {
  const h = await fetch("/api/health").then(r => r.json());
  document.getElementById("mode").textContent =
    "PayPal mode: " + h.paypal_mode + " | AI source: " + h.ai_source +
    " | Stored invoices: " + h.invoices;
}

document.getElementById("create").onclick = async () => {
  const out = document.getElementById("create_out");
  out.textContent = "Working...";
  const r = await post("/api/create", {
    timesheet: document.getElementById("timesheet").value,
    client: document.getElementById("client").value,
    rate: parseFloat(document.getElementById("rate").value),
    due_days: parseInt(document.getElementById("due_days").value, 10),
    recipient_email: document.getElementById("recipient_email").value || null,
  });
  out.textContent = JSON.stringify(r, null, 2);
  loadHealth();
};

document.getElementById("refresh_list").onclick = async () => {
  const r = await fetch("/api/invoices").then(x => x.json());
  document.getElementById("list_out").textContent = JSON.stringify(r, null, 2);
};

document.getElementById("refresh_status").onclick = async () => {
  const r = await post("/api/status", {
    invoice_id: document.getElementById("status_id").value,
  });
  document.getElementById("list_out").textContent = JSON.stringify(r, null, 2);
};

document.getElementById("ask").onclick = async () => {
  const r = await post("/api/ask", {
    question: document.getElementById("question").value,
  });
  document.getElementById("ask_out").textContent = r.answer || JSON.stringify(r);
};

document.getElementById("followup").onclick = async () => {
  const r = await post("/api/followup", {
    invoice_id: document.getElementById("followup_id").value,
    tone: document.getElementById("tone").value,
  });
  document.getElementById("followup_out").textContent = r.draft || JSON.stringify(r);
};

loadHealth();
