const $ = (id) => document.getElementById(id);
const fmt = (n) => (n ?? 0).toLocaleString("en-IN");
const COLORS = {P1: "#c62f2f", P2: "#d97f26", P3: "#b89a2a", P4: "#6e8794"};
const AGENT_COLOR = {guard: "#ff9f8f", intake: "#9fd3ef", geo: "#b9e3a5", verifier: "#d8c7f2", router: "#f2d38a",
  triage: "#ffb08a", resource: "#a5e0d0", commander: "#e7eef1", officer: "#7fd6a8", policy: "#f0b35e",
  info_desk: "#c7d4da", gateway: "#8fa6b2", system: "#ff6b5e"};

// Approximate river courses for the exercise map (not survey data).
const RIVERS = {
  Ravi: [[32.40, 75.80], [32.27, 75.65], [32.18, 75.45], [32.10, 75.25], [32.03, 75.02], [31.95, 74.88], [31.80, 74.70], [31.62, 74.55]],
  Beas: [[32.02, 76.02], [31.94, 75.88], [31.92, 75.70], [31.80, 75.55], [31.62, 75.40], [31.52, 75.30], [31.36, 75.22], [31.22, 75.18], [31.16, 74.97]],
  Sutlej: [[31.39, 76.37], [31.15, 76.48], [30.97, 76.53], [31.02, 76.10], [31.02, 75.79], [31.08, 75.34], [31.16, 74.95], [30.99, 74.55], [30.70, 74.25], [30.40, 74.03]],
};
const WIDTH = {normal: 4, warning: 10, danger: 20, extreme: 32};

const map = L.map("map", {zoomControl: false, attributionControl: true}).setView([31.55, 75.25], 9);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors", maxZoom: 13,
}).addTo(map);

const riverLayers = {};
for (const [name, pts] of Object.entries(RIVERS)) {
  riverLayers[name] = L.polyline(pts, {color: "#2e6e8e", weight: 10, opacity: 0.55, lineCap: "round"}).addTo(map);
}
const pins = {};
const units = {};
const routes = {};
let startedAt = null;
let endedAt = null;

function setGauges(g) {
  const box = $("gauges");
  box.innerHTML = "";
  for (const [river, level] of Object.entries(g)) {
    if (riverLayers[river]) {
      riverLayers[river].setStyle({weight: WIDTH[level] || 6, color: level === "danger" || level === "extreme" ? "#3f8fb8" : "#2e6e8e",
        opacity: level === "extreme" ? 0.8 : 0.6});
    }
    const d = document.createElement("div");
    d.className = `gauge ${level}`;
    d.innerHTML = `${river} gauge<b></b>`;
    d.querySelector("b").textContent = level;
    box.appendChild(d);
  }
}

function upsertIncident(i) {
  if (i.lat == null) return;
  const color = COLORS[i.priority] || "#8fa6b2";
  const done = i.status === "dispatched" || i.status === "info_sent" || i.status === "rejected";
  const style = {radius: i.priority === "P1" ? 9 : 7, color: "#0f1d25", weight: 1, fillColor: color,
    fillOpacity: done ? 0.35 : 0.95};
  // spread pins from the same village a little so they do not stack
  const h = [...i.id].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
  const ll = [i.lat + ((h % 97) / 97 - 0.5) * 0.07, i.lon + (((h >>> 8) % 89) / 89 - 0.5) * 0.09];
  if (pins[i.id]) pins[i.id].setStyle(style);
  else pins[i.id] = L.circleMarker(ll, style).addTo(map).bindTooltip(`${i.id} ${i.village || ""}`);
}

function upsertUnit(a) {
  const icon = L.divIcon({className: "", html: `<div class="unit-icon ${a.status}"></div>`, iconSize: [10, 10]});
  if (units[a.id]) units[a.id].setIcon(icon);
  else units[a.id] = L.marker([a.lat, a.lon], {icon, keyboard: false}).addTo(map).bindTooltip(a.label);
}

function streamLine(e) {
  const ol = $("stream");
  const li = document.createElement("li");
  li.className = `${e.level || "info"} fresh`;
  const id = document.createElement("span"); id.className = "id"; id.textContent = (e.sos_id || "").replace("SOS-", "");
  const ag = document.createElement("span"); ag.className = "ag"; ag.textContent = e.agent;
  ag.style.color = e.level === "info" ? (AGENT_COLOR[e.agent] || "#e7eef1") : "";
  const tx = document.createElement("span"); tx.className = "tx";
  const b = document.createElement("b"); b.textContent = e.action + " ";
  tx.append(b, document.createTextNode(e.detail || ""));
  const tok = document.createElement("span"); tok.className = "tok";
  tok.textContent = e.tokens ? `${fmt(e.tokens)} tok · ${(e.ms / 1000).toFixed(1)}s${e.lane && e.lane !== "fake" ? " · " + e.lane : ""}` :
    (e.lane === "rules" ? "rules" : "");
  li.append(id, ag, tx, tok);
  ol.prepend(li);
  while (ol.children.length > 40) ol.lastChild.remove();
}

function tick(t) {
  const L = t.ledger;
  $("alloc").textContent = fmt(L.remaining);
  $("allocbar").style.width = `${Math.max(0, 100 * L.remaining / L.allocation)}%`;
  $("allocsub").textContent = `${fmt(L.used)} tokens used in ${fmt(L.calls)} model calls`;
  document.querySelector(".alloc").classList.toggle("low", L.low);
  const s = t.stats;
  $("c-recv").textContent = fmt(s.received);
  $("c-disp").textContent = fmt(s.dispatched);
  $("c-block").textContent = fmt(s.blocked);
  $("c-lat").textContent = s.median_s_to_plan != null ? `${s.median_s_to_plan}s` : "–";
  $("qd").textContent = t.queue ? `· ${t.queue} waiting in queue, ${t.in_flight} in flight` : (t.in_flight ? `· ${t.in_flight} in flight` : "");
}

function ticker(text) {
  const tr = $("track");
  tr.textContent = text;
  tr.style.animation = "none";
  void tr.offsetWidth;
  tr.style.animation = "";
}

function findings(r) {
  const c = r.citizen;
  const rt = r.red_team;
  const cells = [
    [fmt(c.n), "citizen SOS triaged"],
    [c.located_pct != null ? `${c.located_pct}%` : "–", "located to the right village"],
    [c.exact_pct != null ? `${c.exact_pct}%` : "–", "priority exactly right"],
    [c.under_pct != null ? `${c.under_pct}%` : "–", "ranked lower than the truth"],
    [`${fmt(rt.stopped)} / ${fmt(rt.n)}`, "red team attacks stopped"],
    [r.latency_median_s != null ? `${r.latency_median_s}s` : "–", "median SOS to plan"],
    [fmt(r.tokens_used), "Neerdhara tokens spent"],
    [r.inr_per_person != null ? `₹${r.inr_per_person}` : `₹${r.cost_inr}`, r.inr_per_person != null ? "model cost per person reached" : "model cost"],
  ];
  $("fgrid").innerHTML = "";
  for (const [v, k] of cells) {
    const d = document.createElement("div");
    const b = document.createElement("b"); b.textContent = v;
    const s = document.createElement("span"); s.textContent = k;
    d.append(b, s);
    $("fgrid").appendChild(d);
  }
  const m = c.misses?.[0];
  $("miss").textContent = m ? `One it got wrong: ${m.sos} was ranked ${m.system}, the truth was ${m.truth}. ` +
    `The card said water at ${m.truth_water}${m.truth_vulnerable.length ? " with " + m.truth_vulnerable.join(", ") : ""}; ` +
    `the system read: "${m.said}".` : "";
  $("findings").classList.remove("hide");
}

setInterval(() => {
  if (!startedAt) return;
  const end = endedAt || Date.now() / 1000;
  const s = Math.max(0, Math.floor(end - startedAt));
  $("clock").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}, 500);

async function boot() {
  const s = await (await fetch("/api/state")).json();
  setGauges(s.gauges);
  s.assets.forEach(upsertUnit);
  s.incidents.forEach(upsertIncident);
  startedAt = s.drill.started_at;
  endedAt = s.drill.ended_at;
  tick({ledger: s.ledger, stats: s.stats, queue: s.queue, in_flight: s.in_flight});
  const last = s.drill.injects[s.drill.injects.length - 1];
  if (last) ticker(last.ticker);

  const es = new EventSource("/api/stream");
  es.addEventListener("agent", (m) => streamLine(JSON.parse(m.data).data));
  es.addEventListener("incident", (m) => upsertIncident(JSON.parse(m.data).data));
  es.addEventListener("tick", (m) => tick(JSON.parse(m.data).data));
  es.addEventListener("score", (m) => findings(JSON.parse(m.data).data));
  es.addEventListener("inject", async (m) => {
    const d = JSON.parse(m.data).data;
    ticker(d.ticker);
    if (d.kind === "exercise_started") { startedAt = d.ts; endedAt = null; $("findings").classList.add("hide"); }
    if (d.kind === "exercise_terminated") endedAt = d.ts;
    const st = await (await fetch("/api/state")).json();
    setGauges(st.gauges);
  });
  es.addEventListener("asset", async () => {
    const st = await (await fetch("/api/state")).json();
    st.assets.forEach(upsertUnit);
  });
  setInterval(async () => {
    const st = await (await fetch("/api/state")).json();
    st.assets.forEach(upsertUnit);
    $("c-p1").textContent = fmt(st.incidents.filter((i) => i.priority === "P1" && i.status === "awaiting_approval").length);
  }, 3000);
}
boot();
