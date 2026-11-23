"""
Turn RAHAT learning-outcome answers into a report: outcomes.csv, two charts and summary.txt.

Sources (pick one):
    --base https://<app> --admin $ADMIN_TOKEN     the live app (GET /api/admin/outcomes/snapshot)
    --snapshots DIR_OR_FILE [...]                  snapshot JSON files saved by the app
    --blob <storage-account>                       download the "outcomes" container with `az`
                                                   (signed in, Storage Blob Data Reader)

    python scripts/outcomes_report.py --blob rahatst0000 --out outcomes_report

Entry and exit are paired by the anonymous id within each run. Standard library plus matplotlib.
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rahat.outcomes import EXIT_ITEMS, QUESTIONS, merge, quiz_score, summarize  # noqa: E402

SERIES, SURFACE, INK, INK_2, GRID = "#2a78d6", "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


# ------------------------------------------------------------------ sources

def from_api(base: str, admin: str) -> list[dict]:
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login = urllib.request.Request(base.rstrip("/") + "/api/login", data=json.dumps({"secret": admin}).encode(),
                                   headers={"Content-Type": "application/json"}, method="POST")
    op.open(login, timeout=30).read()
    with op.open(base.rstrip("/") + "/api/admin/outcomes/snapshot", timeout=60) as r:
        return [json.loads(r.read().decode())]


def from_files(paths: list[str]) -> list[dict]:
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(p.rglob("*.json")) if p.is_dir() else [p]
    snaps = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "answers" in data:
            snaps.append(data)
    return snaps


def from_blob(account: str, container: str) -> list[dict]:
    tmp = tempfile.mkdtemp(prefix="rahat_outcomes_")
    az = "az.cmd" if os.name == "nt" else "az"
    subprocess.run([az, "storage", "blob", "download-batch", "--account-name", account, "-s", container,
                    "-d", tmp, "--auth-mode", "login", "-o", "none"], check=True)
    return from_files([tmp])


# ------------------------------------------------------------------ outputs

def write_csv(path: Path, snap: dict) -> None:
    cols = ["run", "anon_id", "paired", "entry_quiz", "exit_quiz", "entry_c1", "exit_c1", *EXIT_ITEMS,
            "entry_q1", "entry_q2", "entry_q3", "exit_q1", "exit_q2", "exit_q3"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in snap["answers"]:
            e, x = r.get("entry"), r.get("exit")
            w.writerow([r.get("session_id", snap.get("session_id")), r["anon_id"], int(bool(e and x)),
                        quiz_score(e) if e else "", quiz_score(x) if x else "",
                        e["c1"] if e else "", x["c1"] if x else "",
                        *[(x or {}).get(k, "") for k in EXIT_ITEMS],
                        *[(e or {}).get(q, "") for q in ("q1", "q2", "q3")],
                        *[(x or {}).get(q, "") for q in ("q1", "q2", "q3")]])


def bar_chart(path: Path, title: str, entry: float | None, exit_: float | None, top: float, unit: str, n: int):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.2, 3.8), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    vals = [entry or 0, exit_ or 0]
    bars = ax.bar(["Entry check", "Exit debrief"], vals, width=0.5, color=SERIES, zorder=3)
    for b, v in zip(bars, [entry, exit_], strict=True):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + top * 0.02, "n/a" if v is None else f"{v:.2f}",
                ha="center", va="bottom", color=INK, fontsize=10, fontweight="bold")
    ax.set_ylim(0, top * 1.15)  # headroom so the top value label clears the title
    ax.set_yticks([i * top / 5 for i in range(6)])
    ax.set_ylabel(unit, color=INK_2, fontsize=9)
    ax.set_title(f"{title}\npaired participants, n = {n}", color=INK, fontsize=11, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def _f(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _moved(a, b) -> str:
    return "rose" if (a or 0) < (b or 0) else ("fell" if (a or 0) > (b or 0) else "stayed level")


def paragraph(s: dict) -> str:
    q, c, a = s["quiz"], s["confidence"], s["agree_pct"]
    if not s["n_paired"]:
        return (f"Of the participants who joined the exercise, {s['n_entry']} completed the entry check and "
                f"{s['n_exit']} the exit debrief, but no participant completed both, so no paired "
                "comparison of learning is possible from this session.")
    effect = f", standardised effect size d_z = {q['effect_size_dz']:.2f}" if "effect_size_dz" in q else ""
    return (
        f"Learning outcomes were measured with an anonymous three-item knowledge check and a five-point "
        f"self-efficacy item, administered on participants' phones when they joined the live drill and again "
        f"when it was terminated. {s['n_entry']} participants completed the entry check and {s['n_exit']} the "
        f"exit debrief; {s['n_paired']} completed both and form the paired sample. Among them, the mean "
        f"knowledge score {_moved(q['entry_mean'], q['exit_mean'])} from {_f(q['entry_mean'])} to {_f(q['exit_mean'])} out of {q['max']} "
        f"(mean gain {_f(q['mean_gain'])}{effect}), and mean confidence in designing and deploying a "
        f"multi-agent system with monitoring and guardrails {_moved(c['entry_mean'], c['exit_mean'])} from {_f(c['entry_mean'])} to "
        f"{_f(c['exit_mean'])} on a five-point scale. At exit, {_f(a['helped']['pct'], 0)}% agreed that the "
        f"live drill helped them understand how multi-agent systems work, {_f(a['dashboard']['pct'], 0)}% that "
        f"the live dashboard made deployment and monitoring concrete, and {_f(a['recommend']['pct'], 0)}% "
        f"would recommend the format for other AI courses (n = {a['helped']['n']}). These figures describe "
        f"short-term change within a single session, measured by the same items before and after, without a "
        f"comparison group and with voluntary, self-selected responses; they indicate immediate learning and "
        f"perceived value, not lasting retention, and cannot be attributed to the drill alone."
    )


def write_summary(path: Path, s: dict, snap: dict) -> None:
    q, c = s["quiz"], s["confidence"]
    lines = [
        "RAHAT learning outcomes",
        f"Runs: {snap.get('runs', 1)}   sessions: {snap.get('session_id')}",
        f"Entry checks: {s['n_entry']}   exit debriefs: {s['n_exit']}   paired: {s['n_paired']}   "
        f"skipped: entry {s['skipped'].get('entry', 0)}, exit {s['skipped'].get('exit', 0)}",
        f"Quiz (0-{q['max']}), paired: entry {_f(q['entry_mean'])} -> exit {_f(q['exit_mean'])}, "
        f"mean gain {_f(q['mean_gain'])}" + (f", sd {q['sd_gain']}, d_z {q['effect_size_dz']}" if "sd_gain" in q else ""),
        f"Quiz, all respondents: entry {_f(q['entry_mean_all'])} (n {s['n_entry']}), "
        f"exit {_f(q['exit_mean_all'])} (n {s['n_exit']})",
        f"Confidence (1-5), paired: entry {_f(c['entry_mean'])} -> exit {_f(c['exit_mean'])}",
        "Correct per question (entry -> exit, all respondents):",
    ]
    for item in QUESTIONS["quiz"]:
        pq = s["per_question"][item["id"]]
        lines.append(f"  {item['id']} {_f(pq['entry_correct_pct'], 1)}% -> {_f(pq['exit_correct_pct'], 1)}%  "
                     f"{item['text']}")
    lines.append("Agree (4 or 5):")
    for k, v in s["agree_pct"].items():
        lines.append(f"  {k:<10} {_f(v['pct'], 1)}%  (n {v['n']})")
    lines += ["", "Paragraph for the report:", paragraph(s), "",
              f"Comments ({len(s['comments'])}), phone numbers redacted, quote only with permission:"]
    lines += [f"  - {t}" for t in s["comments"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(snaps: list[dict], out: Path, charts: bool = True) -> dict:
    if not snaps:
        raise SystemExit("no outcome snapshots found")
    snap = merge(snaps)
    s = summarize(snap)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "outcomes.csv", snap)
    write_summary(out / "summary.txt", s, snap)
    if charts:
        bar_chart(out / "quiz_entry_vs_exit.png", "Knowledge check score", s["quiz"]["entry_mean"],
                  s["quiz"]["exit_mean"], s["quiz"]["max"], f"Mean score (0 to {s['quiz']['max']})", s["n_paired"])
        bar_chart(out / "confidence_entry_vs_exit.png", "Confidence: design and deploy a multi-agent system",
                  s["confidence"]["entry_mean"], s["confidence"]["exit_mean"], 5, "Mean rating (1 to 5)",
                  s["n_paired"])
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--base", help="live app address")
    src.add_argument("--snapshots", nargs="+", help="snapshot JSON files or folders")
    src.add_argument("--blob", metavar="ACCOUNT", help="storage account holding the outcomes container")
    ap.add_argument("--admin", default=os.getenv("ADMIN_TOKEN", ""), help="ADMIN_TOKEN (default: env)")
    ap.add_argument("--container", default="outcomes")
    ap.add_argument("--out", default="outcomes_report")
    ap.add_argument("--no-charts", action="store_true", help="skip the PNG charts (no matplotlib needed)")
    a = ap.parse_args()

    if a.base:
        if not a.admin:
            ap.error("--base needs --admin or ADMIN_TOKEN in the environment")
        snaps = from_api(a.base, a.admin)
    elif a.blob:
        snaps = from_blob(a.blob, a.container)
    else:
        snaps = from_files(a.snapshots)
    s = build(snaps, Path(a.out), charts=not a.no_charts)
    print(f"paired {s['n_paired']}  entry {s['n_entry']}  exit {s['n_exit']}  ->  {a.out}/")
    print((Path(a.out) / "summary.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
