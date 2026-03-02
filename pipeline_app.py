"""Research Pipeline — standalone Streamlit UI.

Lets you configure, run, and inspect the full research pipeline
(fetch → score → AI reports → similar-paper linking) in one place.

Run with:
    streamlit run pipeline_app.py
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Any

import streamlit as st

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

# ---- project imports ----
from app import (
    JOURNAL_OPTIONS,
    L,
    Paper,
    build_digest,
    build_runtime_prefs_from_settings,
    fetch_candidates,
    llm_enhance_summary,
    paper_id as get_paper_id,
)
from paper_archive import archive_size, find_similar, init_archive, store_paper
from research_pipeline import (
    DEFAULT_ARCHIVE_DB,
    DEFAULT_MAX_REPORTS,
    DEFAULT_SIMILAR_LIMIT,
    _best_link_from_pid,
    _build_slack_text,
    _deliver,
    _generate_report,
)

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Research Pipeline",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lang() -> str:
    return st.session_state.get("lang", "zh")


def _t(zh: str, en: str) -> str:
    return zh if _lang() == "zh" else en


def _label(key: str, zh: str, en: str) -> str:
    return zh if _lang() == "zh" else en


# ---------------------------------------------------------------------------
# Sidebar — settings
# ---------------------------------------------------------------------------

def render_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.markdown("## ⚙️ " + _t("设置", "Settings"))

        # Language
        lang_opt = st.radio(
            _t("界面语言", "Language"),
            ["中文", "English"],
            index=0 if _lang() == "zh" else 1,
            horizontal=True,
        )
        st.session_state.lang = "zh" if lang_opt == "中文" else "en"
        lang = _lang()

        st.divider()
        st.markdown("#### " + _t("📡 抓取范围", "📡 Fetch Scope"))

        keywords_raw = st.text_area(
            _t("关键词（逗号分隔）", "Keywords (comma-separated)"),
            value=st.session_state.get("kw_raw", ""),
            placeholder=_t("例：transformer, protein folding", "e.g. transformer, protein folding"),
            height=80,
            key="kw_raw_input",
        )
        st.session_state.kw_raw = keywords_raw

        exclude_raw = st.text_input(
            _t("排除关键词（逗号分隔）", "Exclude keywords"),
            value=st.session_state.get("excl_raw", "protocol only"),
            key="excl_raw_input",
        )
        st.session_state.excl_raw = exclude_raw

        selected_journals = st.multiselect(
            _t("期刊（可多选）", "Journals"),
            options=JOURNAL_OPTIONS,
            default=st.session_state.get("journals", []),
            key="journals_input",
        )
        st.session_state.journals = selected_journals

        date_days = st.slider(
            _t("抓取窗口（天）", "Date window (days)"),
            min_value=1, max_value=14,
            value=st.session_state.get("date_days", 3),
            key="date_days_input",
        )
        st.session_state.date_days = date_days

        strict_journal = st.checkbox(
            _t("严格期刊匹配", "Strict journal matching"),
            value=st.session_state.get("strict_journal", True),
            key="strict_journal_input",
        )
        st.session_state.strict_journal = strict_journal

        st.divider()
        st.markdown("#### " + _t("🤖 AI 设置", "🤖 AI Settings"))

        api_key = st.text_input(
            _t("OpenAI API Key", "OpenAI API Key"),
            value=st.session_state.get("api_key", os.getenv("OPENAI_API_KEY", "")),
            type="password",
            placeholder="sk-...",
            key="api_key_input",
        )
        st.session_state.api_key = api_key

        api_model = st.selectbox(
            _t("模型", "Model"),
            ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"],
            index=0,
            key="api_model_input",
        )

        max_reports = st.slider(
            _t("精读报告数量上限", "Max deep-read reports"),
            min_value=1, max_value=10,
            value=st.session_state.get("max_reports", DEFAULT_MAX_REPORTS),
            key="max_reports_input",
        )
        st.session_state.max_reports = max_reports

        st.divider()
        st.markdown("#### " + _t("📦 归档 & 推送", "📦 Archive & Push"))

        archive_db = st.text_input(
            _t("归档数据库路径", "Archive DB path"),
            value=st.session_state.get("archive_db", DEFAULT_ARCHIVE_DB),
            key="archive_db_input",
        )
        st.session_state.archive_db = archive_db

        webhook_url = st.text_input(
            _t("Webhook URL（选填，留空则不推送）", "Webhook URL (optional)"),
            value=st.session_state.get("webhook_url", ""),
            type="password",
            placeholder="https://hooks.slack.com/services/...",
            key="webhook_url_input",
        )
        st.session_state.webhook_url = webhook_url

    # Build a settings dict compatible with build_runtime_prefs_from_settings
    settings: dict[str, Any] = {
        "language": lang,
        "keywords": keywords_raw,
        "exclude_keywords": exclude_raw,
        "journals": selected_journals,
        "strict_journal_only": strict_journal,
        "push_schedule": "daily",
        "custom_days": date_days,
        "date_range_days": date_days,
        "openai_api_key": api_key,
        "api_model": api_model,
        "enable_webhook_push": bool(webhook_url.strip()),
        "webhook_url": webhook_url,
    }
    return settings, archive_db, max_reports


# ---------------------------------------------------------------------------
# Pipeline runner (called on button click)
# ---------------------------------------------------------------------------

def _run_pipeline(
    settings: dict[str, Any],
    archive_db: str,
    max_reports: int,
) -> dict[str, Any]:
    """Execute all pipeline steps and return a results dict."""
    lang = settings.get("language", "zh")
    prefs, _ = build_runtime_prefs_from_settings(settings)
    prefs_rt = dict(prefs)

    # 1. Fetch
    papers, fetch_note, fetch_diag, eff = fetch_candidates(prefs_rt)
    prefs_rt["date_range_days"] = int(eff.get("effective_days", prefs_rt.get("date_range_days", 3)))
    prefs_rt["strict_journal_only"] = bool(eff.get("effective_strict_journal_only", prefs_rt.get("strict_journal_only", True)))

    # 2. Digest
    digest = build_digest(prefs_rt, papers)
    top_picks    = digest.get("top_picks", [])
    also_notable = digest.get("also_notable", [])
    all_cards    = list(top_picks) + list(also_notable)

    if not all_cards:
        return {
            "empty": True,
            "fetch_note": fetch_note,
            "fetch_diag": fetch_diag,
            "lang": lang,
        }

    # 3. Archive init
    init_archive(archive_db)
    prev_count = archive_size(archive_db)

    # 4. Per-paper: find similar + generate report (parallel)
    selected = all_cards[:max_reports]

    def _process(c: dict[str, Any]) -> dict[str, Any]:
        pid      = c.get("paper_id", "")
        title    = c.get("title", "")
        abstract = c.get("source_abstract", "")
        similar: list[dict[str, Any]] = []
        if prev_count > 0:
            similar = find_similar(archive_db, title=title, abstract=abstract,
                                   exclude_paper_id=pid, limit=DEFAULT_SIMILAR_LIMIT)
        report = _generate_report(c, settings, prefs_rt)
        store_paper(archive_db, paper_id=pid, title=title, abstract=abstract,
                    venue=c.get("venue", ""), publication_date=c.get("date", ""),
                    report=report)
        return {**c, "report": report, "similar": similar}

    report_cards: list[dict[str, Any]] = []
    max_w = min(4, len(selected))
    if max_w:
        with ThreadPoolExecutor(max_workers=max_w) as ex:
            futs = {ex.submit(_process, c): c for c in selected}
            for fut in as_completed(futs):
                try:
                    report_cards.append(fut.result())
                except Exception as exc:
                    orig = futs[fut]
                    report_cards.append({**orig, "report": {}, "similar": [], "_error": str(exc)})

    # Restore score order
    order = {c.get("paper_id"): i for i, c in enumerate(selected)}
    report_cards.sort(key=lambda x: order.get(x.get("paper_id", ""), 999))

    # Store also-notable papers in archive without full report
    report_pids = {rc.get("paper_id") for rc in report_cards}
    for c in all_cards:
        if c.get("paper_id") not in report_pids:
            store_paper(archive_db, paper_id=c.get("paper_id", ""),
                        title=c.get("title", ""), abstract=c.get("source_abstract", ""),
                        venue=c.get("venue", ""), publication_date=c.get("date", ""),
                        report={})

    also_for_push = [c for c in all_cards if c.get("paper_id") not in report_pids]

    date_str   = datetime.now(UTC).strftime("%Y-%m-%d")
    slack_text = _build_slack_text(
        date_str=date_str,
        report_cards=report_cards,
        also_notable=also_for_push,
        total_fetched=len(all_cards),
        lang=lang,
    )

    return {
        "empty": False,
        "date_str": date_str,
        "fetch_note": fetch_note,
        "fetch_diag": fetch_diag,
        "total_fetched": len(all_cards),
        "report_cards": report_cards,
        "also_notable": also_for_push,
        "slack_text": slack_text,
        "archive_total": archive_size(archive_db),
        "lang": lang,
    }


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------

def _render_score_bar(score: float, label: str) -> None:
    pct = min(100, max(0, int(score)))
    color = "#4CAF50" if pct >= 70 else "#FF9800" if pct >= 45 else "#9E9E9E"
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
              <span style="font-size:0.78rem;width:70px;color:#666">{label}</span>
              <div style="flex:1;background:#eee;border-radius:4px;height:8px">
                <div style="width:{pct}%;background:{color};height:8px;border-radius:4px"></div>
              </div>
              <span style="font-size:0.78rem;color:#666;width:30px;text-align:right">{pct}</span>
            </div>""",
        unsafe_allow_html=True,
    )


def render_report_card(rc: dict[str, Any], idx: int, lang: str, expanded: bool = False) -> None:
    title   = rc.get("title", "")
    venue   = rc.get("venue", "")
    date    = rc.get("date", "")
    link    = rc.get("link", "")
    rpt     = rc.get("report", {})
    similar = rc.get("similar", [])
    scores  = rc.get("scores", {})
    abstract = rc.get("source_abstract", "")

    methods    = rpt.get("methods_detailed",  "") or rc.get("methods_in_one_line", "")
    conclusion = rpt.get("main_conclusion",   "") or rc.get("main_conclusion", "")
    future     = rpt.get("future_direction",  "") or rc.get("future_direction", "")
    value      = rpt.get("value_assessment",  "") or rc.get("value_assessment", "")
    ai_sum     = rpt.get("ai_feed_summary",   "") or rc.get("ai_feed_summary", "")

    header = f"{idx}. {title}"
    if venue:
        header += f"  ·  {venue}"

    with st.expander(header, expanded=expanded):
        # Top row: meta + link
        col_meta, col_link = st.columns([4, 1])
        with col_meta:
            st.markdown(
                f"<span style='color:#888;font-size:0.85rem'>📅 {date}</span>",
                unsafe_allow_html=True,
            )
        with col_link:
            if link:
                st.link_button(L(lang, "阅读原文 🔗", "Read 🔗"), link, use_container_width=True)

        # Scores
        if scores:
            st.markdown(
                f"<div style='font-size:0.78rem;color:#888;margin-top:4px;margin-bottom:2px'>"
                + L(lang, "评分", "Scores") + "</div>",
                unsafe_allow_html=True,
            )
            score_cols = st.columns(4)
            labels_zh = ["相关", "新颖", "严谨", "影响"]
            labels_en = ["Relevance", "Novelty", "Rigor", "Impact"]
            keys = ["relevance", "novelty", "rigor", "impact"]
            for col, lz, le, k in zip(score_cols, labels_zh, labels_en, keys):
                with col:
                    _render_score_bar(scores.get(k, 0), L(lang, lz, le))

        st.divider()

        # AI report sections
        if ai_sum:
            st.markdown("**" + L(lang, "🤖 AI 总结", "🤖 AI Summary") + "**")
            st.info(ai_sum)

        report_cols = st.columns(2)
        with report_cols[0]:
            if methods:
                st.markdown("**" + L(lang, "🔬 方法", "🔬 Methods") + "**")
                st.write(methods)
            if conclusion:
                st.markdown("**" + L(lang, "💡 核心结论", "💡 Key Conclusion") + "**")
                st.write(conclusion)

        with report_cols[1]:
            if future:
                st.markdown("**" + L(lang, "🚀 未来方向", "🚀 Future Directions") + "**")
                st.write(future)
            if value:
                st.markdown("**" + L(lang, "⭐ 研究价值", "⭐ Research Value") + "**")
                st.write(value)

        # Abstract (collapsed)
        if abstract:
            with st.expander(L(lang, "摘要", "Abstract"), expanded=False):
                st.write(abstract)

        # Similar papers
        if similar:
            st.markdown("**" + L(lang, "📎 相关历史论文", "📎 Related Past Papers") + "**")
            for s in similar:
                stitle = s.get("title", "")
                svenue = s.get("venue", "")
                sdate  = s.get("date", "")
                spid   = s.get("paper_id", "")
                sscore = s.get("score", 0)
                slink  = _best_link_from_pid(spid)
                sim_pct = int(sscore * 100)
                if slink:
                    label = f"[{stitle}]({slink})"
                else:
                    label = stitle
                st.markdown(
                    f"- {label}  "
                    f"<span style='color:#888;font-size:0.8rem'>({svenue}, {sdate} · {L(lang,'相似度','sim')} {sim_pct}%)</span>",
                    unsafe_allow_html=True,
                )


def render_results(results: dict[str, Any], archive_db: str) -> None:
    lang = results.get("lang", "zh")

    if results.get("empty"):
        st.warning(L(lang,
            "今日未筛选到符合条件的论文。可尝试放宽关键词或扩大日期窗口。",
            "No papers matched filters today. Try broadening keywords or the date window."))
        diag = results.get("fetch_diag", {})
        if diag:
            with st.expander(L(lang, "抓取诊断", "Fetch diagnostics")):
                st.json(diag)
        return

    date_str      = results["date_str"]
    total_fetched = results["total_fetched"]
    report_cards  = results["report_cards"]
    also_notable  = results["also_notable"]
    slack_text    = results["slack_text"]
    archive_total = results["archive_total"]

    # Summary stats
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(L(lang, "今日论文", "Papers Today"), total_fetched)
    col2.metric(L(lang, "精读报告", "Deep-Read Reports"), len(report_cards))
    col3.metric(L(lang, "其他关注", "Also Notable"), len(also_notable))
    col4.metric(L(lang, "累计归档", "Archive Total"), archive_total)

    st.caption(results.get("fetch_note", ""))
    st.divider()

    # Deep-read report cards
    if report_cards:
        st.subheader("📑 " + L(lang, "精读推荐", "Deep Read"))
        for i, rc in enumerate(report_cards, start=1):
            render_report_card(rc, i, lang, expanded=(i == 1))

    # Also notable (compact list)
    if also_notable:
        st.divider()
        st.subheader("📋 " + L(lang, "其他值得关注", "Also Notable"))
        for i, c in enumerate(also_notable, start=len(report_cards) + 1):
            title  = c.get("title", "")
            venue  = c.get("venue", "")
            date   = c.get("date", "")
            link   = c.get("link", "")
            summary = c.get("ai_feed_summary", "") or c.get("value_assessment", "")
            cols = st.columns([8, 1])
            with cols[0]:
                head = f"**{i}. {title}**  ·  {venue}, {date}"
                st.markdown(head)
                if summary:
                    st.caption(summary)
            with cols[1]:
                if link:
                    st.link_button(L(lang, "阅读", "Read"), link, use_container_width=True)

    # Slack / webhook message preview
    st.divider()
    with st.expander(L(lang, "📤 Slack 消息预览 / 下载", "📤 Slack Message Preview / Download")):
        st.code(slack_text, language="text")
        st.download_button(
            label=L(lang, "下载消息文本", "Download message text"),
            data=slack_text.encode("utf-8"),
            file_name=f"research_digest_{date_str}.txt",
            mime="text/plain",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # ---- init session state ----
    if "pipeline_results" not in st.session_state:
        st.session_state.pipeline_results = None
    if "pipeline_running" not in st.session_state:
        st.session_state.pipeline_running = False

    # ---- sidebar ----
    settings, archive_db, max_reports = render_sidebar()
    lang = settings.get("language", "zh")

    # ---- header ----
    st.title("📚 " + L(lang, "Research Pipeline", "Research Pipeline"))
    st.caption(L(lang,
        "抓取今日论文 · AI 精读报告 · 历史相似论文关联 · Slack 推送",
        "Fetch today's papers · AI deep-read reports · similar-paper linking · Slack push"))

    # ---- archive info bar ----
    n_archived = archive_size(archive_db) if archive_db else 0
    info_col, run_col = st.columns([3, 1])
    with info_col:
        st.info(
            L(lang,
              f"📦 归档库：**{n_archived}** 篇历史论文  |  路径：`{archive_db}`",
              f"📦 Archive: **{n_archived}** historical papers  |  path: `{archive_db}`"),
            icon="📂",
        )
    with run_col:
        run_clicked = st.button(
            "🚀 " + L(lang, "运行流程", "Run Pipeline"),
            type="primary",
            use_container_width=True,
            disabled=st.session_state.pipeline_running,
        )

    st.divider()

    # ---- run pipeline on button click ----
    if run_clicked:
        st.session_state.pipeline_running = True
        st.session_state.pipeline_results = None

        with st.status(
            L(lang, "⏳ 正在运行研究流程…", "⏳ Running research pipeline…"),
            expanded=True,
        ) as status:
            st.write(L(lang, "📡 正在抓取论文…", "📡 Fetching papers…"))
            try:
                results = _run_pipeline(settings, archive_db, max_reports)
            except Exception as exc:
                status.update(
                    label=L(lang, f"❌ 运行出错：{exc}", f"❌ Pipeline error: {exc}"),
                    state="error",
                )
                st.session_state.pipeline_running = False
                st.stop()

            if results.get("empty"):
                status.update(
                    label=L(lang, "⚠️ 今日无符合条件的论文", "⚠️ No matching papers today"),
                    state="complete",
                )
            else:
                n = len(results.get("report_cards", []))
                st.write(L(lang,
                    f"✅ 生成了 {n} 篇精读报告，归档 {results.get('archive_total', 0)} 篇",
                    f"✅ Generated {n} deep-read reports; archive now {results.get('archive_total', 0)} papers"))

                # Push to webhook if configured
                webhook_url = settings.get("webhook_url", "").strip()
                if webhook_url:
                    st.write(L(lang, "📤 正在推送至 Webhook…", "📤 Pushing to webhook…"))
                    ok, msg = _deliver(
                        webhook_override="",
                        settings=settings,
                        lang=lang,
                        date_str=results["date_str"],
                        text=results["slack_text"],
                    )
                    if ok:
                        st.write(L(lang, f"✅ 推送成功：{msg}", f"✅ Push OK: {msg}"))
                    else:
                        st.write(L(lang, f"⚠️ 推送失败：{msg}", f"⚠️ Push failed: {msg}"))

                status.update(
                    label=L(lang, "✅ 流程完成！", "✅ Pipeline complete!"),
                    state="complete",
                )

        st.session_state.pipeline_results = results
        st.session_state.pipeline_running = False

    # ---- render results ----
    if st.session_state.pipeline_results:
        render_results(st.session_state.pipeline_results, archive_db)
    else:
        st.markdown(
            "<div style='text-align:center;color:#aaa;padding:60px 0'>"
            + L(lang, '点击"运行流程"按钮开始', "Click Run Pipeline to start")
            + "</div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
