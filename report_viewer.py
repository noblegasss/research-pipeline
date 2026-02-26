"""Generate a self-contained Notion-style HTML viewer from the paper archive.

Reads all pipeline runs stored in the SQLite archive and produces a single
HTML file with:
  - Left sidebar listing every run date (newest on top)
  - Main panel showing that day's deep-read reports and also-notable papers
  - Expandable cards: methods / conclusion / future / value / similar papers

Usage
-----
    python report_viewer.py                          # writes research_report.html
    python report_viewer.py --archive paper_archive.db --output my_report.html
    python report_viewer.py --open                   # open in browser after generating
"""
from __future__ import annotations

import argparse
import json
import os
import re
import webbrowser
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from paper_archive import get_run, list_runs

_DEFAULT_ARCHIVE = os.getenv("RESEARCH_ARCHIVE_DB", "paper_archive.db")
_DEFAULT_OUTPUT = "research_report.html"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _best_link(card: dict[str, Any]) -> str:
    pid  = card.get("paper_id", "")
    link = card.get("link", "")
    if link:
        return link
    if pid.startswith("doi:"):
        return f"https://doi.org/{pid[4:]}"
    if pid.startswith("arxiv:"):
        return f"https://arxiv.org/abs/{pid[6:]}"
    if pid.startswith("pmid:"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{pid[5:]}/"
    return ""


def _score_pct(scores: dict[str, Any], key: str) -> int:
    return min(100, max(0, int(scores.get(key, 0))))


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

def _esc(s: Any) -> str:
    return escape(str(s or ""), quote=True)


def _render_score_bars(scores: dict[str, Any]) -> str:
    bars = []
    for key, label_zh, label_en in [
        ("relevance", "相关", "Relevance"),
        ("novelty",   "新颖", "Novelty"),
        ("rigor",     "严谨", "Rigor"),
        ("impact",    "影响", "Impact"),
    ]:
        pct = _score_pct(scores, key)
        color = "#4caf50" if pct >= 70 else "#ff9800" if pct >= 45 else "#9e9e9e"
        bars.append(
            f'<div class="score-row">'
            f'<span class="score-label" data-zh="{label_zh}" data-en="{label_en}">{label_zh}</span>'
            f'<div class="score-track"><div class="score-fill" style="width:{pct}%;background:{color}"></div></div>'
            f'<span class="score-num">{pct}</span>'
            f"</div>"
        )
    return "\n".join(bars)


def _render_similar(similar: list[dict[str, Any]]) -> str:
    if not similar:
        return ""
    items = []
    for s in similar:
        title  = _esc(s.get("title", ""))
        venue  = _esc(s.get("venue", ""))
        date   = _esc(s.get("date", ""))
        pid    = s.get("paper_id", "")
        score  = int(s.get("score", 0) * 100)
        link   = ""
        if pid.startswith("doi:"):
            link = f"https://doi.org/{pid[4:]}"
        elif pid.startswith("arxiv:"):
            link = f"https://arxiv.org/abs/{pid[6:]}"
        elif pid.startswith("pmid:"):
            link = f"https://pubmed.ncbi.nlm.nih.gov/{pid[5:]}/"
        anchor = f'<a href="{_esc(link)}" target="_blank" rel="noopener">{title}</a>' if link else title
        items.append(
            f'<li>{anchor} '
            f'<span class="sim-meta">({venue}, {date} · sim {score}%)</span></li>'
        )
    joined = "\n".join(items)
    return (
        f'<div class="section-block similar-block">'
        f'<h4 class="section-label">'
        f'<span data-zh="📎 相关历史论文" data-en="📎 Related Past Papers">📎 相关历史论文</span>'
        f"</h4>"
        f'<ul class="similar-list">{joined}</ul>'
        f"</div>"
    )


def _render_section(icon: str, label_zh: str, label_en: str, text: str) -> str:
    if not text:
        return ""
    return (
        f'<div class="section-block">'
        f'<h4 class="section-label">{icon} '
        f'<span data-zh="{_esc(label_zh)}" data-en="{_esc(label_en)}">{_esc(label_zh)}</span>'
        f"</h4>"
        f'<p class="section-text">{_esc(text)}</p>'
        f"</div>"
    )


def _render_report_card(card: dict[str, Any], idx: int) -> str:
    title    = _esc(card.get("title", ""))
    venue    = _esc(card.get("venue", ""))
    date     = _esc(card.get("date", ""))
    link     = _best_link(card)
    rpt      = card.get("report", {})
    scores   = card.get("scores", {})
    abstract = card.get("source_abstract", "")
    similar  = card.get("similar", [])
    tags     = card.get("tags", [])

    methods    = rpt.get("methods_detailed",  "") or card.get("methods_in_one_line", "")
    conclusion = rpt.get("main_conclusion",   "") or card.get("main_conclusion", "")
    future     = rpt.get("future_direction",  "") or card.get("future_direction", "")
    value      = rpt.get("value_assessment",  "") or card.get("value_assessment", "")
    ai_sum     = rpt.get("ai_feed_summary",   "") or card.get("ai_feed_summary", "")

    link_btn = (
        f'<a class="read-btn" href="{_esc(link)}" target="_blank" rel="noopener">'
        f'<span data-zh="阅读原文" data-en="Read">阅读原文</span> ↗</a>'
        if link else ""
    )

    tags_html = "".join(
        f'<span class="tag">{_esc(t)}</span>' for t in (tags or [])[:5]
    )

    score_total = _score_pct(scores, "total") if "total" in scores else 0
    tier_color = "#4caf50" if score_total >= 70 else "#ff9800" if score_total >= 45 else "#9e9e9e"

    abstract_block = (
        f'<details class="abstract-details">'
        f'<summary><span data-zh="摘要" data-en="Abstract">摘要</span></summary>'
        f'<p class="abstract-text">{_esc(abstract)}</p>'
        f"</details>"
        if abstract else ""
    )

    return f"""
<article class="paper-card" id="card-{idx}">
  <header class="card-header">
    <div class="card-index">{idx}</div>
    <div class="card-meta">
      <h3 class="card-title">{title}</h3>
      <div class="card-sub">
        <span class="venue-badge">{venue}</span>
        <span class="date-badge">📅 {date}</span>
        <span class="tier-dot" style="background:{tier_color}" title="Score {score_total}"></span>
        {tags_html}
      </div>
    </div>
    {link_btn}
  </header>

  <div class="card-scores">{_render_score_bars(scores)}</div>

  {"<div class='ai-summary'>" + _esc(ai_sum) + "</div>" if ai_sum else ""}

  <div class="card-body">
    <div class="report-grid">
      <div class="report-col">
        {_render_section("🔬", "方法", "Methods", methods)}
        {_render_section("💡", "核心结论", "Key Conclusion", conclusion)}
      </div>
      <div class="report-col">
        {_render_section("🚀", "未来方向", "Future Directions", future)}
        {_render_section("⭐", "研究价值", "Research Value", value)}
      </div>
    </div>
    {abstract_block}
    {_render_similar(similar)}
  </div>
</article>
"""


def _render_also_notable(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return ""
    rows = []
    for i, c in enumerate(cards, start=1):
        title   = _esc(c.get("title", ""))
        venue   = _esc(c.get("venue", ""))
        date    = _esc(c.get("date", ""))
        link    = _best_link(c)
        summary = _esc(c.get("ai_feed_summary", "") or c.get("value_assessment", ""))
        anchor  = f'<a href="{_esc(link)}" target="_blank" rel="noopener">{title}</a>' if link else title
        rows.append(
            f'<div class="notable-row">'
            f'<span class="notable-idx">{i}</span>'
            f'<div class="notable-body">'
            f'<div class="notable-title">{anchor}</div>'
            f'<div class="notable-meta">{venue} · {date}</div>'
            + (f'<div class="notable-summary">{summary}</div>' if summary else "")
            + f"</div></div>"
        )
    joined = "\n".join(rows)
    return f"""
<section class="also-notable">
  <h2 class="section-heading">
    <span data-zh="其他值得关注" data-en="Also Notable">其他值得关注</span>
  </h2>
  {joined}
</section>
"""


def _render_run_page(run: dict[str, Any]) -> str:
    report_cards = run.get("report_cards", [])
    also_notable = run.get("also_notable", [])
    total        = run.get("total_count", len(report_cards) + len(also_notable))
    run_date     = _esc(run.get("run_date", ""))
    slack_text   = run.get("slack_text", "")

    cards_html = "\n".join(
        _render_report_card(c, i) for i, c in enumerate(report_cards, start=1)
    )
    also_html  = _render_also_notable(also_notable)

    stats = (
        f'<div class="run-stats">'
        f'<span class="stat"><span data-zh="今日" data-en="Today">今日</span> <strong>{total}</strong>'
        f' <span data-zh="篇" data-en="papers">篇</span></span> · '
        f'<span class="stat"><strong>{len(report_cards)}</strong>'
        f' <span data-zh="篇精读报告" data-en="deep-read reports">篇精读报告</span></span>'
        f"</div>"
    )

    slack_block = (
        f'<details class="slack-details">'
        f'<summary><span data-zh="Slack 消息文本" data-en="Slack message text">Slack 消息文本</span></summary>'
        f'<pre class="slack-pre">{_esc(slack_text)}</pre>'
        f"</details>"
        if slack_text else ""
    )

    deep_heading = (
        '<h2 class="section-heading">'
        '<span data-zh="📑 精读推荐" data-en="📑 Deep Read">📑 精读推荐</span>'
        "</h2>"
        if report_cards else ""
    )

    return f"""
<div class="run-page" id="run-{run_date}">
  <div class="run-page-header">
    <h1 class="run-date">{run_date}</h1>
    {stats}
  </div>
  {deep_heading}
  {cards_html}
  {also_html}
  {slack_block}
</div>
"""


# ---------------------------------------------------------------------------
# Full HTML document
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Log</title>
<style>
/* ── Reset & base ──────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:15px}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  background:#fff;color:#1a1a1a;display:flex;height:100vh;overflow:hidden}
a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}

/* ── Layout ────────────────────────────────────────────────── */
#sidebar{width:240px;min-width:200px;background:#f7f6f3;border-right:1px solid #e8e7e4;
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0;transition:width .2s}
#sidebar.collapsed{width:0;min-width:0;border:none}
#main{flex:1;overflow-y:auto;display:flex;flex-direction:column}
.top-bar{display:flex;align-items:center;gap:8px;padding:10px 16px;
  border-bottom:1px solid #e8e7e4;background:#fff;position:sticky;top:0;z-index:10}
#toggle-btn{background:none;border:none;cursor:pointer;font-size:1.1rem;
  color:#666;padding:4px;border-radius:4px}
#toggle-btn:hover{background:#eee}
.top-title{font-weight:600;font-size:0.95rem}
.lang-btn{margin-left:auto;background:#f0f0ee;border:none;cursor:pointer;
  padding:4px 10px;border-radius:6px;font-size:0.8rem;color:#555}
.lang-btn:hover{background:#e4e3e0}
#content{flex:1;padding:32px 48px;max-width:880px;margin:0 auto;width:100%}

/* ── Sidebar ───────────────────────────────────────────────── */
.sidebar-head{padding:16px 14px 8px;font-size:0.72rem;font-weight:600;
  letter-spacing:.06em;color:#999;text-transform:uppercase}
.run-list{overflow-y:auto;flex:1;padding-bottom:12px}
.run-item{display:flex;align-items:center;gap:8px;padding:7px 14px;
  cursor:pointer;font-size:0.88rem;color:#444;border-radius:4px;margin:1px 6px;
  transition:background .15s}
.run-item:hover{background:#ece9e3}
.run-item.active{background:#e4e0d9;font-weight:600;color:#111}
.run-item-date{flex:1;font-variant-numeric:tabular-nums}
.run-item-count{font-size:0.75rem;color:#999;background:#e8e7e4;
  padding:1px 6px;border-radius:10px}
.run-item-dot{width:6px;height:6px;border-radius:50%;background:#4caf50;flex-shrink:0}

/* ── Run page ──────────────────────────────────────────────── */
.run-page{display:none}.run-page.active{display:block}
.run-page-header{margin-bottom:28px}
.run-date{font-size:1.8rem;font-weight:700;color:#1a1a1a;margin-bottom:6px}
.run-stats{font-size:0.85rem;color:#888}
.section-heading{font-size:1.1rem;font-weight:600;margin:28px 0 12px;
  padding-bottom:6px;border-bottom:1px solid #e8e7e4}

/* ── Paper card ────────────────────────────────────────────── */
.paper-card{border:1px solid #e8e7e4;border-radius:8px;margin-bottom:16px;
  overflow:hidden;transition:box-shadow .2s}
.paper-card:hover{box-shadow:0 2px 12px rgba(0,0,0,.08)}
.card-header{display:flex;align-items:flex-start;gap:12px;padding:14px 16px 10px;
  background:#fafaf9;border-bottom:1px solid #f0efe9}
.card-index{width:26px;height:26px;border-radius:50%;background:#e8e7e4;
  display:flex;align-items:center;justify-content:center;font-size:0.78rem;
  font-weight:700;flex-shrink:0;margin-top:2px;color:#555}
.card-meta{flex:1;min-width:0}
.card-title{font-size:0.97rem;font-weight:600;line-height:1.4;color:#111;margin-bottom:6px}
.card-sub{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.venue-badge{background:#e3f2fd;color:#1565c0;font-size:0.75rem;
  padding:2px 7px;border-radius:10px;font-weight:500}
.date-badge{font-size:0.75rem;color:#888}
.tier-dot{width:8px;height:8px;border-radius:50%}
.tag{background:#f0efe9;color:#666;font-size:0.72rem;padding:1px 6px;border-radius:8px}
.read-btn{flex-shrink:0;background:#111;color:#fff;font-size:0.78rem;
  padding:5px 12px;border-radius:6px;white-space:nowrap;align-self:center}
.read-btn:hover{background:#333;text-decoration:none}

/* ── Scores ────────────────────────────────────────────────── */
.card-scores{display:flex;flex-wrap:wrap;gap:8px 20px;
  padding:10px 16px;background:#fafaf9;border-bottom:1px solid #f0efe9}
.score-row{display:flex;align-items:center;gap:6px;min-width:160px}
.score-label{font-size:0.73rem;color:#888;width:42px;flex-shrink:0}
.score-track{flex:1;height:5px;background:#e8e7e4;border-radius:3px;overflow:hidden}
.score-fill{height:5px;border-radius:3px;transition:width .4s}
.score-num{font-size:0.72rem;color:#aaa;width:24px;text-align:right}

/* ── AI summary ────────────────────────────────────────────── */
.ai-summary{margin:0 16px;padding:10px 12px;background:#fffbf0;border-left:3px solid #f59e0b;
  border-radius:0 6px 6px 0;font-size:0.85rem;color:#555;line-height:1.5}

/* ── Card body ─────────────────────────────────────────────── */
.card-body{padding:12px 16px 16px}
.report-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 20px}
@media(max-width:600px){.report-grid{grid-template-columns:1fr}}
.section-block{margin-bottom:12px}
.section-label{font-size:0.78rem;font-weight:600;color:#888;margin-bottom:4px;
  text-transform:uppercase;letter-spacing:.04em}
.section-text{font-size:0.87rem;color:#333;line-height:1.6}
.abstract-details{margin-top:10px;border:1px solid #e8e7e4;border-radius:6px;overflow:hidden}
.abstract-details summary{padding:7px 12px;cursor:pointer;font-size:0.8rem;color:#888;
  background:#fafaf9;list-style:none;user-select:none}
.abstract-details summary::-webkit-details-marker{display:none}
.abstract-details[open] summary{border-bottom:1px solid #e8e7e4}
.abstract-text{padding:10px 12px;font-size:0.85rem;color:#444;line-height:1.7}

/* ── Similar papers ────────────────────────────────────────── */
.similar-block{margin-top:10px;padding-top:10px;border-top:1px solid #f0efe9}
.similar-list{list-style:none;display:flex;flex-direction:column;gap:5px}
.similar-list li{font-size:0.83rem;color:#444;line-height:1.4}
.sim-meta{color:#aaa;font-size:0.78rem}

/* ── Also notable ──────────────────────────────────────────── */
.also-notable{margin-top:20px}
.notable-row{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #f0efe9}
.notable-idx{font-size:0.78rem;color:#aaa;width:20px;flex-shrink:0;padding-top:2px}
.notable-title{font-size:0.88rem;font-weight:500;margin-bottom:3px}
.notable-meta{font-size:0.75rem;color:#999;margin-bottom:3px}
.notable-summary{font-size:0.82rem;color:#666;line-height:1.5}

/* ── Slack block ───────────────────────────────────────────── */
.slack-details{margin-top:24px;border:1px solid #e8e7e4;border-radius:6px;overflow:hidden}
.slack-details summary{padding:8px 14px;cursor:pointer;font-size:0.82rem;color:#888;
  background:#fafaf9;list-style:none}
.slack-details summary::-webkit-details-marker{display:none}
.slack-pre{padding:14px;font-size:0.8rem;background:#f7f6f3;overflow-x:auto;
  white-space:pre-wrap;word-break:break-word;color:#444;line-height:1.6}

/* ── Empty state ───────────────────────────────────────────── */
.empty-state{text-align:center;padding:80px 20px;color:#bbb;font-size:0.95rem}
.empty-icon{font-size:3rem;margin-bottom:12px}
</style>
</head>
<body>

<nav id="sidebar">
  <div class="sidebar-head">📚 Research Log</div>
  <div class="run-list" id="run-list"></div>
</nav>

<div id="main">
  <div class="top-bar">
    <button id="toggle-btn" title="Toggle sidebar">☰</button>
    <span class="top-title">Research Log</span>
    <button class="lang-btn" id="lang-btn">EN</button>
  </div>
  <div id="content">
    <div class="empty-state" id="empty-state">
      <div class="empty-icon">📭</div>
      <div data-zh="选择左侧日期查看报告" data-en="Select a date from the sidebar">
        选择左侧日期查看报告
      </div>
    </div>
    __RUN_PAGES__
  </div>
</div>

<script>
const DATA = __DATA_JSON__;
let lang = "zh";

// ── Language toggle ────────────────────────────────────────────
function applyLang(l) {
  lang = l;
  document.querySelectorAll("[data-zh][data-en]").forEach(el => {
    el.textContent = el.dataset[l] || el.textContent;
  });
  document.getElementById("lang-btn").textContent = l === "zh" ? "EN" : "中";
}
document.getElementById("lang-btn").addEventListener("click", () => {
  applyLang(lang === "zh" ? "en" : "zh");
});

// ── Sidebar toggle ─────────────────────────────────────────────
const sidebar = document.getElementById("sidebar");
document.getElementById("toggle-btn").addEventListener("click", () => {
  sidebar.classList.toggle("collapsed");
});

// ── Build sidebar list ─────────────────────────────────────────
function buildSidebar() {
  const list = document.getElementById("run-list");
  DATA.runs.forEach(r => {
    const item = document.createElement("div");
    item.className = "run-item";
    item.dataset.date = r.date;
    item.innerHTML =
      `<span class="run-item-dot"></span>` +
      `<span class="run-item-date">${r.date}</span>` +
      `<span class="run-item-count">${r.total}</span>`;
    item.addEventListener("click", () => showRun(r.date));
    list.appendChild(item);
  });
}

// ── Show run ───────────────────────────────────────────────────
function showRun(date) {
  document.getElementById("empty-state").style.display = "none";
  document.querySelectorAll(".run-page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".run-item").forEach(i => i.classList.remove("active"));

  const page = document.getElementById("run-" + date);
  if (page) {
    page.classList.add("active");
    page.scrollIntoView({behavior:"instant", block:"start"});
    document.getElementById("content").scrollTop = 0;
  }
  const item = document.querySelector(`.run-item[data-date="${date}"]`);
  if (item) item.classList.add("active");
}

// ── Init ───────────────────────────────────────────────────────
buildSidebar();
if (DATA.runs.length > 0) {
  showRun(DATA.runs[0].date);
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_html(archive_db: str = _DEFAULT_ARCHIVE, output: str = _DEFAULT_OUTPUT) -> str:
    """Generate the HTML viewer and write to *output*. Returns the output path."""
    runs_meta = list_runs(archive_db)
    if not runs_meta:
        print(f"[warn] No runs found in archive '{archive_db}'. Run the pipeline first.")

    run_pages_html: list[str] = []
    sidebar_data: list[dict[str, Any]] = []

    for meta in runs_meta:
        run_date  = meta["run_date"]
        total     = meta["total_count"]
        run       = get_run(archive_db, run_date)
        if not run:
            continue
        run_pages_html.append(_render_run_page(run))
        sidebar_data.append({"date": run_date, "total": total})

    data_json = json.dumps({"runs": sidebar_data}, ensure_ascii=False)

    html = _HTML_TEMPLATE.replace("__RUN_PAGES__", "\n".join(run_pages_html))
    html = html.replace("__DATA_JSON__", data_json)

    Path(output).write_text(html, encoding="utf-8")
    print(f"[ok] Report written to: {output}  ({len(runs_meta)} runs, {len(run_pages_html)} rendered)")
    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Notion-style HTML report from paper archive.")
    parser.add_argument("--archive", default=_DEFAULT_ARCHIVE,
        help=f"SQLite archive DB path (default: {_DEFAULT_ARCHIVE})")
    parser.add_argument("--output", default=_DEFAULT_OUTPUT,
        help=f"Output HTML file path (default: {_DEFAULT_OUTPUT})")
    parser.add_argument("--open", action="store_true",
        help="Open the generated HTML in the default browser.")
    args = parser.parse_args()

    out = generate_html(archive_db=args.archive, output=args.output)
    if args.open:
        webbrowser.open(Path(out).resolve().as_uri())


if __name__ == "__main__":
    main()
