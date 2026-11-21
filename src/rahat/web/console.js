const $ = (id) => document.getElementById(id);
const fmt = (n) => (n ?? 0).toLocaleString("en-IN");
let filter = "awaiting_approval,blocked";
let isAdmin = true;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

async function loadCases() {
  const r = await fetch(`/api/incidents?status=${encodeURIComponent(filter)}&limit=120`);
  if (r.status === 401) { location.reload(); return; }
  const items = await r.json();
  const box = $("cases");
  box.innerHTML = "";
  if (!items.length) { box.appendChild(el("p", "muted", "No cases in this view.")); return; }
  for (const i of items) {
    const card = el("article", "case");
    const blocked = i.status === "blocked";
    const pr = blocked ? "review" : (i.triage?.priority || "…");
    card.appendChild(el("div", `prio ${blocked ? "blocked" : (i.triage?.priority || "")}`, pr));
    const mid = el("div");
    mid.appendChild(el("div", "brief", blocked ? `Manual review: ${i.blocked_reason}` :
      (i.plan?.officer_brief || i.extraction?.summary_en || "Processing…")));
    mid.appendChild(el("div", "raw", `“${i.sos.text}”`));
    const meta = [
      i.sos.id,
      i.location?.village ? `${i.location.village}, ${i.location.district}` : "location unresolved",
      i.assignment?.asset_label ? `${i.assignment.asset_label}, ETA ${i.assignment.eta_min} min` : (i.assignment?.note || ""),
      i.triage ? `${i.triage.model_tier} model: ${i.triage.reason}` : "",
      Object.keys(i.callback || {}).length ? `callback ${Object.values(i.callback).join(", ")}` : "",
      i.degraded ? "SOP rules only" : "",
    ].filter(Boolean).join("   |   ");
    mid.appendChild(el("div", "meta", meta));
    card.appendChild(mid);
    const act = el("div", "actions");
    if (i.status === "awaiting_approval" && !i.assignment?.asset_id) {
      act.appendChild(el("span", "muted", "Waiting for a free unit"));
      const no = el("button", "btn ghost small", "Reject");
      no.onclick = () => decide(i.sos.id, false);
      act.appendChild(no);
    } else if (i.status === "awaiting_approval" || blocked) {
      const ok = el("button", "btn ok small", blocked ? "Release" : "Approve");
      const no = el("button", "btn ghost small", "Reject");
      ok.onclick = () => decide(i.sos.id, true);
      no.onclick = () => decide(i.sos.id, false);
      act.append(ok, no);
    } else {
      act.appendChild(el("span", "muted", i.status.replace("_", " ")));
    }
    card.appendChild(act);
    box.appendChild(card);
  }
}

async function decide(id, approve) {
  await fetch(`/api/incidents/${id}/decision`, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({approve})});
  loadCases();
}

async function loadState() {
  const r = await fetch("/api/state");
  if (!r.ok) return;
  const s = await r.json();
  isAdmin = s.role === "admin";
  $("admin").classList.toggle("hide", !isAdmin);
  const st = s.stats;
  $("sub").textContent = `${s.org} allocation on ${s.lane.name}${s.lane.fallback ? `, fallback ${s.lane.fallback}` : ""}. ` +
    `${st.received} received, ${s.queue} queued, ${s.in_flight} in flight, ${st.dispatched} dispatched.`;
  const L = s.ledger;
  $("ledger").innerHTML = "";
  for (const [k, v] of [["Allocated", L.allocation], ["Used", L.used], ["Remaining", L.remaining], ["Model calls", L.calls]]) {
    $("ledger").append(el("span", "", k), el("b", "", fmt(v)));
  }
  for (const [a, v] of Object.entries(L.by_agent)) $("ledger").append(el("span", "muted", `  ${a}`), el("b", "", fmt(v)));
  const ses = $("session");
  ses.innerHTML = "";
  if (s.session) {
    ses.append(el("div", "kv"));
    ses.firstChild.append(el("span", "", "Join code"), el("b", "", s.session.code),
      el("span", "", "Phones joined"), el("b", "", fmt(s.session.joined)),
      el("span", "", "Session"), el("b", "", s.session.live ? "open" : "closed"));
    if (isAdmin && s.session.live) {
      const q = el("div", "qr");
      const img = new Image();
      img.alt = "Join QR code";
      img.src = `/api/admin/qr.svg?c=${s.session.code}`;
      q.appendChild(img);
      ses.appendChild(q);
    }
  }
  const R = $("router");
  if (Object.keys(s.router).length) {
    R.innerHTML = "";
    for (const [ctx, arms] of Object.entries(s.router)) {
      for (const [tier, a] of Object.entries(arms)) {
        R.append(el("span", "", `${ctx} case, ${tier}`), el("b", "", `${Math.round(a.accept_rate * 100)}% · ${a.pulls}`));
      }
    }
  }
  const au = await (await fetch("/api/audit?limit=40")).json();
  $("audit").innerHTML = "";
  for (const a of au) {
    const t = new Date(a.ts * 1000).toLocaleTimeString("en-IN", {hour12: false});
    $("audit").appendChild(el("div", "", `${t}  ${a.event}${a.sos_id ? "  " + a.sos_id : ""}  (${a.actor})`));
  }
}

document.querySelectorAll("#filters .chip").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#filters .chip").forEach((x) => x.setAttribute("aria-pressed", x === b));
    filter = b.dataset.f;
    loadCases();
  };
});

document.querySelectorAll("#admin [data-a]").forEach((b) => {
  b.onclick = async () => {
    const a = b.dataset.a;
    $("aerr").textContent = "";
    if ((a === "reset" || a === "end") && !confirm(a === "reset" ? "Wipe all cases and refill the allocation?" : "Terminate the exercise for everyone?")) return;
    let url = `/api/admin/inject/${a}`, body = {};
    if (a === "session") { url = "/api/admin/session"; body = {minutes: 90}; }
    if (a === "reset") url = "/api/admin/reset";
    const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (r.status === 401) { isAdmin = false; $("admin").classList.add("hide"); return; }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) $("aerr").textContent = j.detail || "Failed";
    if (a === "session" && r.ok) $("aerr").textContent = `Officer code: ${j.officer_code}`;
    loadState(); loadCases();
  };
});

loadCases(); loadState();
setInterval(loadCases, 2500);
setInterval(loadState, 3000);
