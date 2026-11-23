const $ = (id) => document.getElementById(id);
const STEPS = ["queued", "processing", "awaiting_approval", "dispatched"];
let maxChars = 600;

async function refresh() {
  let me;
  try {
    me = await (await fetch("/api/me", {cache: "no-store"})).json();
  } catch { return; }
  if (!me.joined) { location.href = "/?closed=1"; return; }

  showCheck(me);
  $("waiting").classList.toggle("hide", me.started || me.ended);
  $("ended").classList.toggle("hide", !me.ended);
  $("banner").classList.toggle("hide", !me.banner);
  $("banner").textContent = me.banner || "";
  maxChars = me.max_chars;
  $("max").textContent = maxChars;
  $("t").maxLength = maxChars;
  $("send").disabled = !me.started || me.ended || me.sent >= me.limit;
  $("sent").textContent = `${me.sent} of ${me.limit}`;

  const p = me.persona;
  $("card").textContent = p.card;
  $("persona").classList.toggle("role-red", p.role === "red_team");
  $("persona").classList.toggle("role-media", p.role === "media");
  $("role-title").textContent = p.role === "citizen" ? `Your situation · ${p.id}` :
    p.role === "red_team" ? `Red team · ${p.id}` : `Media · ${p.id}`;

  const list = $("list");
  if (!me.messages.length) return;
  list.innerHTML = "";
  for (const m of me.messages) {
    const done = STEPS.indexOf(m.status);
    const el = document.createElement("div");
    el.className = "msg";
    el.innerHTML = `<div class="said"></div><div class="stage"></div>
      <div class="steps">${STEPS.map((_, i) => `<i class="${i <= done ? "on" : ""}"></i>`).join("")}</div>`;
    el.querySelector(".said").textContent = `${m.id}: ${m.text}`;
    el.querySelector(".stage").textContent = m.position ? `${m.stage} Position ${m.position} in queue.` : m.stage;
    if (m.reply) {
      const r = document.createElement("div");
      r.className = "reply";
      r.textContent = m.reply;
      el.appendChild(r);
    }
    list.appendChild(el);
  }
}

$("t").addEventListener("input", () => { $("len").textContent = $("t").value.length; });

$("send").onclick = async () => {
  const text = $("t").value.trim();
  $("err").textContent = "";
  if (text.length < 3) { $("err").textContent = "Write a few words: where you are and what is happening."; return; }
  $("send").disabled = true;
  const r = await fetch("/api/sos", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text})});
  const body = await r.json().catch(() => ({}));
  if (r.ok) { $("t").value = ""; $("len").textContent = "0"; }
  else $("err").textContent = body.detail || "Not sent. Try again.";
  refresh();
};

$("askbtn").onclick = async () => {
  const question = $("q").value.trim();
  $("qerr").textContent = "";
  if (question.length < 3) return;
  $("askbtn").disabled = true;
  const r = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({question})});
  const body = await r.json().catch(() => ({}));
  $("askbtn").disabled = false;
  if (r.ok) { $("answer").classList.remove("hide"); $("answertext").textContent = body.answer; }
  else $("qerr").textContent = body.detail || "No answer. Try again.";
};

document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
    $("tab-send").classList.toggle("hide", b.dataset.tab !== "send");
    $("tab-ask").classList.toggle("hide", b.dataset.tab !== "ask");
  };
});

// ---------- learning check: entry on join, debrief when the exercise is terminated ----------
// A random id kept on this phone is the only key. No name or phone number goes with the answers.
function anonId() {
  const make = () => (crypto.randomUUID ? crypto.randomUUID() :
    Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""));
  try {
    let id = localStorage.getItem("rahat_anon");
    if (!id) { id = make(); localStorage.setItem("rahat_anon", id); }
    return id;
  } catch { return (window._rahatAnon ||= make()); }
}

let questions = null;
let checkPhase = null;   // "entry" | "exit" | null: which form is on screen
let checkClosed = { entry: false, exit: false };  // answered or skipped on this page load

async function loadQuestions() {
  if (!questions) questions = await (await fetch("/api/outcomes/questions")).json();
  return questions;
}

function scaleField(id, text, lo = "Strongly disagree", hi = "Strongly agree") {
  const fs = document.createElement("fieldset");
  fs.innerHTML = `<legend></legend><div class="scale">${[1, 2, 3, 4, 5].map((n) =>
    `<label><input type="radio" name="${id}" value="${n}">${n}</label>`).join("")}</div>
    <div class="ends"><span>${lo}</span><span>${hi}</span></div>`;
  fs.querySelector("legend").textContent = text;
  return fs;
}

function buildForm(phase, q) {
  const form = $("check-form");
  form.innerHTML = "";
  q.quiz.forEach((item, i) => {
    const fs = document.createElement("fieldset");
    const lg = document.createElement("legend");
    lg.textContent = `${i + 1}. ${item.text}`;
    fs.appendChild(lg);
    for (const [k, v] of Object.entries(item.options)) {
      const l = document.createElement("label");
      l.className = "opt";
      l.innerHTML = `<input type="radio" name="${item.id}" value="${k}"><span></span>`;
      l.querySelector("span").textContent = `(${k}) ${v}`;
      fs.appendChild(l);
    }
    form.appendChild(fs);
  });
  form.appendChild(scaleField(q.confidence.id, `"${q.confidence.text}"`));
  if (phase === "exit") {
    for (const it of q.exit_items) form.appendChild(scaleField(it.id, it.text));
    const fs = document.createElement("fieldset");
    fs.innerHTML = `<legend>Any comment? (optional, no names or numbers)</legend>
      <textarea name="comment" maxlength="500"></textarea>`;
    form.appendChild(fs);
  }
}

async function showCheck(me) {
  const o = me.outcomes || {};
  let phase = null;
  if (me.ended && !o.exit_done && !checkClosed.exit) phase = "exit";
  else if (!me.ended && !o.entry_done && !checkClosed.entry) phase = "entry";
  if (phase === checkPhase) return;
  checkPhase = phase;
  $("check").classList.toggle("hide", !phase);
  if (!phase) return;
  $("check-thanks").classList.add("hide");
  const q = await loadQuestions();
  $("check-title").textContent = phase === "entry" ? "Before we start: quick check" : "Debrief: quick check";
  $("check-intro").textContent = phase === "entry" ?
    "Four quick taps, under a minute. Anonymous, not graded. Guess if you don't know." :
    "Same questions as before, plus four more. Anonymous, not graded.";
  $("check-err").textContent = "";
  buildForm(phase, q);
  $("check").scrollIntoView({behavior: "smooth", block: "start"});
}

function closeCheck(thanks) {
  checkClosed[checkPhase] = true;
  checkPhase = null;
  $("check").classList.add("hide");
  $("check-thanks").classList.toggle("hide", !thanks);
}

$("check-send").onclick = async () => {
  const phase = checkPhase;
  if (!phase) return;
  const fd = new FormData($("check-form"));
  const need = ["q1", "q2", "q3", "c1", ...(phase === "exit" ? ["helped", "dashboard", "recommend"] : [])];
  const missing = need.filter((k) => !fd.get(k));
  if (missing.length) { $("check-err").textContent = "Please answer every question, or press Skip."; return; }
  const body = {anon_id: anonId()};
  for (const k of need) body[k] = /^q/.test(k) ? fd.get(k) : Number(fd.get(k));
  if (phase === "exit") body.comment = (fd.get("comment") || "").trim();
  $("check-send").disabled = true;
  const r = await fetch(`/api/outcomes/${phase}`, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)});
  $("check-send").disabled = false;
  if (r.ok || r.status === 409) closeCheck(true);
  else $("check-err").textContent = "Not saved. Try again, or press Skip.";
};

$("check-skip").onclick = async () => {
  const phase = checkPhase;
  if (!phase) return;
  closeCheck(false);
  fetch("/api/outcomes/skip", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({phase})}).catch(() => {});
};

refresh();
setInterval(refresh, 3000);
