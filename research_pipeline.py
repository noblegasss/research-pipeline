"""Research pipeline: fetch → AI-select → per-paper report → similar-paper linking → push.

Builds on the existing fetch/digest infrastructure in app.py and adds:
  1. Per-paper detailed AI reports (methods, conclusion, future direction, value)
  2. Historical similarity linking via paper_archive.py
  3. A rich, structured Slack/webhook push format

Usage
-----
Single user (from a prefs JSON file):
    python research/research_pipeline.py --prefs user_prefs.json
    python research/research_pipeline.py --prefs user_prefs.json \\
        --webhook https://hooks.slack.com/... --max-reports 5

Multi-user auto-push (processes all due subscriptions):
    python research/research_pipeline.py --all-due

Environment variables
---------------------
OPENAI_API_KEY        Required for AI report generation.
WEBHOOK_URL           Fallback webhook URL when not set in prefs.
RESEARCH_ARCHIVE_DB   Path to the SQLite archive (default: paper_archive.db).
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

# Allow importing app.py from a sibling research_push directory.
# If your folder layout differs, set RESEARCH_PUSH_ROOT explicitly.
# Override with env var RESEARCH_PUSH_ROOT if the layout is different.
import os as _os
_ROOT = Path(_os.environ.get(
    "RESEARCH_PUSH_ROOT",
    str(Path(__file__).resolve().parent.parent / "research_push"),
))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from paper_archive import archive_size, find_similar, init_archive, store_paper, store_run
from app import (
    L,
    Paper,
    best_link,
    build_digest,
    build_runtime_prefs_from_settings,
    fetch_candidates,
    list_due_auto_push_subscriptions,
    llm_enhance_summary,
    mark_auto_push_run,
    paper_id as get_paper_id,
    release_auto_push_slot,
)

DEFAULT_ARCHIVE_DB = os.getenv("RESEARCH_ARCHIVE_DB", "paper_archive.db")
DEFAULT_MAX_REPORTS = 5
DEFAULT_SIMILAR_LIMIT = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _openai_key(settings: dict[str, Any]) -> str:
    key = str(settings.get("openai_api_key", "")).strip()
    return key or os.getenv("OPENAI_API_KEY", "").strip()


def _api_model(settings: dict[str, Any]) -> str:
    return str(settings.get("api_model", "gpt-4.1-mini")).strip() or "gpt-4.1-mini"


def _best_link_from_pid(paper_id: str, fallback_link: str = "") -> str:
    """Reconstruct a URL from a paper_id string (doi:/arxiv: prefix)."""
    if paper_id.startswith("doi:"):
        return f"https://doi.org/{paper_id[4:]}"
    if paper_id.startswith("arxiv:"):
        return f"https://arxiv.org/abs/{paper_id[6:]}"
    if paper_id.startswith("pmid:"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{paper_id[5:]}/"
    return fallback_link


# ---------------------------------------------------------------------------
# Per-paper AI report generation
# ---------------------------------------------------------------------------

def _generate_report(
    card: dict[str, Any],
    settings: dict[str, Any],
    prefs: dict[str, Any],
) -> dict[str, str]:
    """Call llm_enhance_summary for one card; fall back to card fields on failure."""
    api_key = _openai_key(settings)
    model = _api_model(settings)

    if api_key:
        p = Paper(
            title=card.get("title", ""),
            authors=[],
            venue=card.get("venue", ""),
            publication_date=card.get("date", ""),
            abstract=card.get("source_abstract", ""),
        )
        sc = card.get("scores", {})
        result = llm_enhance_summary(p, sc, prefs, api_key, model)
        if result:
            return result

    # Graceful fallback — return whatever the card already computed
    return {
        "methods_detailed": card.get("methods_detailed", card.get("methods_in_one_line", "")),
        "main_conclusion": card.get("main_conclusion", ""),
        "future_direction": card.get("future_direction", ""),
        "value_assessment": card.get("value_assessment", ""),
        "ai_feed_summary": card.get("ai_feed_summary", ""),
    }


# ---------------------------------------------------------------------------
# Slack / webhook formatting
# ---------------------------------------------------------------------------

def _push_slack(webhook_url: str, text: str, lang: str = "zh") -> tuple[bool, str]:
    """Send a pre-formatted text block to a Slack incoming webhook."""
    try:
        resp = requests.post(
            webhook_url.strip(),
            json={"text": text[:39_000]},  # Slack payload limit ≈ 40 KB
            timeout=15,
        )
        if 200 <= resp.status_code < 300:
            body = (resp.text or "").strip()
            if body and len(body) <= 120:
                return True, L(lang,
                    f"推送成功（HTTP {resp.status_code}，响应：{body}）",
                    f"Push succeeded (HTTP {resp.status_code}, response: {body})")
            return True, L(lang,
                f"推送成功（HTTP {resp.status_code}）",
                f"Push succeeded (HTTP {resp.status_code})")
        body = (resp.text or "").strip()
        bp = f"，响应：{body[:180]}" if body else ""
        return False, L(lang,
            f"推送失败（HTTP {resp.status_code}{bp}）",
            f"Push failed (HTTP {resp.status_code}{', response: ' + body[:180] if body else ''})")
    except Exception as exc:
        return False, L(lang, f"推送异常：{exc}", f"Push error: {exc}")


def _push_generic(webhook_url: str, payload: dict[str, Any], lang: str = "zh") -> tuple[bool, str]:
    """Send a JSON payload to a generic webhook."""
    try:
        resp = requests.post(webhook_url.strip(), json=payload, timeout=15)
        if 200 <= resp.status_code < 300:
            return True, L(lang,
                f"推送成功（HTTP {resp.status_code}）",
                f"Push succeeded (HTTP {resp.status_code})")
        return False, L(lang,
            f"推送失败（HTTP {resp.status_code}）",
            f"Push failed (HTTP {resp.status_code})")
    except Exception as exc:
        return False, L(lang, f"推送异常：{exc}", f"Push error: {exc}")


def _build_slack_text(
    date_str: str,
    report_cards: list[dict[str, Any]],
    also_notable: list[dict[str, Any]],
    total_fetched: int,
    lang: str,
) -> str:
    """Render the full digest as a plain-text Slack message."""
    lines: list[str] = []

    lines.append(L(lang, f"📚 今日研究快报 | {date_str}", f"📚 Research Digest | {date_str}"))
    lines.append("━" * 36)

    n_report = len(report_cards)
    n_notable = len(also_notable)
    lines.append(L(lang,
        f"今日筛选 {total_fetched} 篇 · 精读推荐 {n_report} 篇 · 其他关注 {n_notable} 篇",
        f"Fetched {total_fetched} papers · {n_report} deep reads · {n_notable} also notable"))
    lines.append("")

    if report_cards:
        lines.append(L(lang, "━━ 精读推荐 ━━", "━━ Deep Read ━━"))
        lines.append("")
        for i, rc in enumerate(report_cards, start=1):
            rpt = rc.get("report", {})
            title = rc.get("title", "")
            venue = rc.get("venue", "")
            date  = rc.get("date", "")
            link  = rc.get("link", "")
            similar = rc.get("similar", [])

            methods   = rpt.get("methods_detailed", "") or rc.get("methods_in_one_line", "")
            conclusion = rpt.get("main_conclusion", "") or rc.get("main_conclusion", "")
            future    = rpt.get("future_direction", "") or rc.get("future_direction", "")
            value     = rpt.get("value_assessment", "") or rc.get("value_assessment", "")
            ai_sum    = rpt.get("ai_feed_summary", "") or rc.get("ai_feed_summary", "")

            lines.append(f"{i}. *{title}*")
            lines.append(L(lang, f"   📖 {venue} | {date}", f"   📖 {venue} | {date}"))

            if methods:
                lines.append(L(lang, f"   🔬 方法：{methods}", f"   🔬 Methods: {methods}"))
            if conclusion:
                lines.append(L(lang, f"   💡 结论：{conclusion}", f"   💡 Conclusion: {conclusion}"))
            if future:
                lines.append(L(lang, f"   🚀 未来：{future}", f"   🚀 Future: {future}"))
            if value:
                lines.append(L(lang, f"   ⭐ 价值：{value}", f"   ⭐ Value: {value}"))
            # Only show AI summary when it adds something beyond value_assessment
            if ai_sum and ai_sum != value:
                lines.append(L(lang, f"   🤖 AI摘要：{ai_sum}", f"   🤖 AI summary: {ai_sum}"))
            if link:
                lines.append(f"   🔗 {link}")

            if similar:
                parts: list[str] = []
                for s in similar:
                    stitle = (s.get("title") or "")[:50]
                    svenue = s.get("venue", "")
                    sdate  = s.get("date", "")
                    spid   = s.get("paper_id", "")
                    slink  = _best_link_from_pid(spid)
                    label  = f"「{stitle}」({svenue}, {sdate})"
                    if slink:
                        label += f" {slink}"
                    parts.append(label)
                lines.append(L(lang,
                    f"   📎 相关论文：" + " | ".join(parts),
                    f"   📎 Related: " + " | ".join(parts)))

            lines.append("")

    if also_notable:
        lines.append(L(lang, "━━ 其他值得关注 ━━", "━━ Also Notable ━━"))
        lines.append("")
        base = len(report_cards) + 1
        for i, c in enumerate(also_notable, start=base):
            title = c.get("title", "")
            venue = c.get("venue", "")
            date  = c.get("date", "")
            link  = c.get("link", "")
            summary = c.get("ai_feed_summary", "") or c.get("value_assessment", "")
            head = f"{i}. {title} ({venue}, {date})"
            if link:
                head += f" | {link}"
            lines.append(head)
            if summary:
                lines.append(f"   {summary}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_research_once(
    settings: dict[str, Any],
    webhook_override: str = "",
    archive_db: str = DEFAULT_ARCHIVE_DB,
    max_reports: int = DEFAULT_MAX_REPORTS,
    report_dir: str = "",
) -> tuple[bool, str]:
    """Full research pipeline for one subscription / prefs set.

    Returns (ok, message).
    """
    prefs, plan = build_runtime_prefs_from_settings(settings)
    lang = str(prefs.get("language", "zh"))

    # ------------------------------------------------------------------
    # 1. Fetch and filter papers
    # ------------------------------------------------------------------
    papers, fetch_note, fetch_diag, effective_filter = fetch_candidates(prefs)
    prefs_rt = dict(prefs)
    prefs_rt["date_range_days"] = int(
        effective_filter.get("effective_days", prefs.get("date_range_days", 1))
    )
    prefs_rt["strict_journal_only"] = bool(
        effective_filter.get("effective_strict_journal_only", prefs.get("strict_journal_only", True))
    )

    # ------------------------------------------------------------------
    # 2. Build digest (score + rank + journal balancing)
    # ------------------------------------------------------------------
    digest = build_digest(prefs_rt, papers)
    top_picks    = digest.get("top_picks", [])
    also_notable = digest.get("also_notable", [])
    all_cards    = list(top_picks) + list(also_notable)
    total_fetched = len(all_cards)

    if not all_cards:
        msg = L(lang,
            "今日未筛选到符合条件的论文，已跳过研究报告生成。",
            "No papers matched filters today; research report skipped.")
        print(msg)
        _deliver(webhook_override, settings, lang,
                 date_str=datetime.now(UTC).strftime("%Y-%m-%d"),
                 text=msg)
        return True, msg

    print(L(lang,
        f"[1/4] 抓取完成：{fetch_note} | 总计 {total_fetched} 篇",
        f"[1/4] Fetch complete: {fetch_note} | {total_fetched} papers"))

    # ------------------------------------------------------------------
    # 3. Initialise archive
    # ------------------------------------------------------------------
    init_archive(archive_db)
    prev_count = archive_size(archive_db)
    print(L(lang,
        f"[2/4] 归档库已就绪，当前收录 {prev_count} 篇历史论文",
        f"[2/4] Archive ready — {prev_count} historical papers"))

    # ------------------------------------------------------------------
    # 4. Select papers for detailed reports (top N by score)
    # ------------------------------------------------------------------
    selected_for_report: list[dict[str, Any]] = all_cards[:max_reports]

    # ------------------------------------------------------------------
    # 5. Generate per-paper reports + find similar (parallel)
    # ------------------------------------------------------------------
    def _process_one(c: dict[str, Any]) -> dict[str, Any]:
        pid      = c.get("paper_id", "")
        title    = c.get("title", "")
        abstract = c.get("source_abstract", "")

        # Find similar BEFORE storing this paper so we never self-match
        similar: list[dict[str, Any]] = []
        if prev_count > 0:
            similar = find_similar(
                archive_db,
                title=title,
                abstract=abstract,
                exclude_paper_id=pid,
                limit=DEFAULT_SIMILAR_LIMIT,
            )

        # Detailed AI report
        report = _generate_report(c, settings, prefs_rt)

        # Persist to archive
        store_paper(
            archive_db,
            paper_id=pid,
            title=title,
            abstract=abstract,
            venue=c.get("venue", ""),
            publication_date=c.get("date", ""),
            report=report,
        )

        return {**c, "report": report, "similar": similar}

    report_cards: list[dict[str, Any]] = []
    max_workers = min(4, len(selected_for_report))
    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_process_one, c): c for c in selected_for_report}
            for fut in as_completed(futs):
                try:
                    report_cards.append(fut.result())
                except Exception as exc:
                    orig = futs[fut]
                    print(f"[warn] report failed for '{orig.get('title', '')[:40]}': {exc}",
                          file=sys.stderr)
                    report_cards.append({**orig, "report": {}, "similar": []})

    # Restore score-order (futures may complete out of order)
    order = {c.get("paper_id"): i for i, c in enumerate(selected_for_report)}
    report_cards.sort(key=lambda x: order.get(x.get("paper_id", ""), 999))

    # Also archive notable-only papers (no detailed report needed)
    report_pids = {rc.get("paper_id") for rc in report_cards}
    for c in all_cards:
        if c.get("paper_id") not in report_pids:
            store_paper(
                archive_db,
                paper_id=c.get("paper_id", ""),
                title=c.get("title", ""),
                abstract=c.get("source_abstract", ""),
                venue=c.get("venue", ""),
                publication_date=c.get("date", ""),
                report={},
            )

    also_for_push = [c for c in all_cards if c.get("paper_id") not in report_pids]

    print(L(lang,
        f"[3/4] 报告生成完成：{len(report_cards)} 篇精读报告，归档库现有 {archive_size(archive_db)} 篇",
        f"[3/4] Reports done: {len(report_cards)} deep-read reports; archive now {archive_size(archive_db)} papers"))

    # ------------------------------------------------------------------
    # 6. Optionally save per-paper JSON files
    # ------------------------------------------------------------------
    if report_dir:
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        for rc in report_cards:
            safe = re.sub(r"[^\w\-]", "_", rc.get("paper_id", "unknown"))[:80]
            path = Path(report_dir) / f"{safe}.json"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rc, fh, ensure_ascii=False, indent=2)
        print(L(lang,
            f"[3/4] 单篇报告已保存至 {report_dir}/",
            f"[3/4] Per-paper JSON reports saved to {report_dir}/"))

    # ------------------------------------------------------------------
    # 7. Build Slack message
    # ------------------------------------------------------------------
    date_str   = datetime.now(UTC).strftime("%Y-%m-%d")
    slack_text = _build_slack_text(
        date_str=date_str,
        report_cards=report_cards,
        also_notable=also_for_push,
        total_fetched=total_fetched,
        lang=lang,
    )

    # ------------------------------------------------------------------
    # 7b. Persist run to archive for the HTML viewer
    # ------------------------------------------------------------------
    store_run(archive_db, date_str, report_cards, also_for_push, slack_text)

    # ------------------------------------------------------------------
    # 8. Deliver
    # ------------------------------------------------------------------
    ok, msg = _deliver(webhook_override, settings, lang, date_str, slack_text, digest)
    if not ok:
        return False, msg
    print(L(lang, f"[4/4] {msg}", f"[4/4] {msg}"))
    return True, "ok"


def _deliver(
    webhook_override: str,
    settings: dict[str, Any],
    lang: str,
    date_str: str = "",
    text: str = "",
    digest: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Resolve webhook URL and send."""
    url = webhook_override.strip()
    if not url and bool(settings.get("enable_webhook_push", False)):
        url = str(settings.get("webhook_url", "")).strip()
    if not url:
        url = os.getenv("WEBHOOK_URL", "").strip()
    if not url:
        return False, L(lang,
            "无推送目标，请在设置中填写 webhook URL 或设置 WEBHOOK_URL 环境变量。",
            "No delivery target. Set webhook_url in settings or WEBHOOK_URL env var.")

    if "hooks.slack.com/services/" in url:
        return _push_slack(url, text, lang)

    # Generic webhook: send full structured payload
    payload: dict[str, Any] = {
        "date": date_str,
        "today_new_summary": text,
        "worth_reading_summary": text,
    }
    if digest is not None:
        payload["digest"] = digest
    return _push_generic(url, payload, lang)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research pipeline: fetch, generate per-paper reports, link to history, push."
    )
    parser.add_argument("--prefs", default="",
        help="Path to user preferences JSON (required unless --all-due).")
    parser.add_argument("--all-due", action="store_true",
        help="Process all due auto-push subscriptions.")
    parser.add_argument("--limit", type=int, default=0,
        help="Max subscriptions to process in --all-due mode.")
    parser.add_argument("--webhook", default="",
        help="Webhook URL override.")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE_DB,
        help=f"SQLite archive DB path (default: {DEFAULT_ARCHIVE_DB}).")
    parser.add_argument("--max-reports", type=int, default=DEFAULT_MAX_REPORTS,
        help=f"Max papers to generate detailed AI reports for (default: {DEFAULT_MAX_REPORTS}).")
    parser.add_argument("--report-dir", default="",
        help="Directory to save individual per-paper JSON report files.")
    args = parser.parse_args()

    if args.all_due:
        due = list_due_auto_push_subscriptions()
        if args.limit > 0:
            due = due[: args.limit]
        if not due:
            print("No due subscriptions.")
            return
        success = failed = 0
        for item in due:
            sid = item["subscriber_id"]
            ok, msg = run_research_once(
                settings=item["settings"],
                archive_db=args.archive,
                max_reports=args.max_reports,
                report_dir=args.report_dir,
            )
            if ok:
                mark_auto_push_run(sid, item["local_date"], "")
                success += 1
                print(f"[ok] {sid[:8]} {item['timezone']} {item['local_time']}")
            else:
                release_auto_push_slot(sid, item["local_date"], msg)
                failed += 1
                print(f"[failed] {sid[:8]}: {msg}")
        if failed:
            raise SystemExit(f"Completed with failures. success={success}, failed={failed}")
        print(f"Completed. success={success}, failed=0")
        return

    if not args.prefs:
        raise SystemExit("--prefs is required unless --all-due is used.")

    with open(args.prefs, "r", encoding="utf-8") as fh:
        settings = json.load(fh)

    ok, msg = run_research_once(
        settings=settings,
        webhook_override=args.webhook,
        archive_db=args.archive,
        max_reports=args.max_reports,
        report_dir=args.report_dir,
    )
    if not ok:
        raise SystemExit(msg)
    print(msg)


if __name__ == "__main__":
    main()
