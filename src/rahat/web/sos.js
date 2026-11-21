const $ = (id) => document.getElementById(id);
const STEPS = ["queued", "processing", "awaiting_approval", "dispatched"];
let maxChars = 600;

async function refresh() {
  let me;
  try {
    me = await (await fetch("/api/me", {cache: "no-store"})).json();
  } catch { return; }
  if (!me.joined) { location.href = "/?closed=1"; return; }

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

refresh();
setInterval(refresh, 3000);
