"""FastAPI backend for the Research Pipeline web app."""
from __future__ import annotations

import json
import math
import os
import re
import sys
import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator

import threading
import time
from datetime import UTC, datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Python path ────────────────────────────────────────────────────────────
_ROOT = Path(os.environ.get(
    "RESEARCH_PUSH_ROOT",
    str(Path(__file__).resolve().parent.parent.parent / "research_push"),
))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

# ── Streamlit stub ─────────────────────────────────────────────────────────
try:
    import streamlit  # noqa: F401
except ModuleNotFoundError:
    _api_dir = Path(__file__).resolve().parent
    if str(_api_dir) not in sys.path:
        sys.path.insert(0, str(_api_dir))
    import st_stub as _st_stub
    sys.modules.setdefault("streamlit", _st_stub)
    sys.modules.setdefault("streamlit.errors", _st_stub)
    import types as _types
    _sje = _types.ModuleType("streamlit_js_eval")
    _sje.streamlit_js_eval = None  # type: ignore[attr-defined]
    sys.modules.setdefault("streamlit_js_eval", _sje)

from paper_archive import archive_size, find_similar, get_run, init_archive, list_runs, store_paper

try:
    from app import JOURNAL_OPTIONS, FIELD_OPTIONS
except Exception:
    JOURNAL_OPTIONS = ["Nature", "Science", "Cell", "NEJM", "JAMA", "arXiv"]
    FIELD_OPTIONS = ["AI", "Machine Learning", "Healthcare", "Biology", "Medicine"]

CONFIG_FILE = _PIPELINE_DIR / "pipeline_config.json"
NOTES_META_FILE = _PIPELINE_DIR / "notes_meta.json"
DEFAULT_ARCHIVE = os.getenv("RESEARCH_ARCHIVE_DB", str(_PIPELINE_DIR / "paper_archive.db"))
REPORTS_DIR = _PIPELINE_DIR / "reports"
NOTES_DIR = _PIPELINE_DIR / "notes"

app = FastAPI(title="Research Pipeline API", version="1.0.0")

# ── Background pipeline task state ────────────────────────────────────────
_pipeline_lock = threading.Lock()
_pipeline_state: dict[str, Any] = {
    "status": "idle",   # idle | running | done | error
    "logs": [],
    "date": None,
    "total": 0,
    "reports": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def _pipeline_log(msg: str) -> None:
    _pipeline_state["logs"].append(msg)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Config helpers ─────────────────────────────────────────────────────────

def _load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _save_config(cfg: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


def _best_link(card: dict[str, Any]) -> str:
    pid = card.get("paper_id", "")
    if pid.startswith("doi:"):
        return f"https://doi.org/{pid[4:]}"
    if pid.startswith("arxiv:"):
        return f"https://arxiv.org/abs/{pid[6:]}"
    if pid.startswith("pmid:"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{pid[5:]}/"
    link = (card.get("link") or "").strip()
    return link if link.startswith("http") else ""


def _enrich_links(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cards:
        c = dict(c)
        c["link"] = _best_link(c)
        out.append(c)
    return out


def _safe_slug(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:max_len] or "paper"


def _find_paper_report_url(paper_id: str, title: str) -> str | None:
    """Return the app URL /reports/{date}/{slug} if a report file exists for this paper."""
    if not REPORTS_DIR.exists():
        return None
    slug = _safe_slug(title)
    # Search newest dates first
    for date_dir in sorted(REPORTS_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        if (date_dir / f"{slug}.md").exists():
            return f"/reports/{date_dir.name}/{slug}"
    return None


# ── Figure extraction from ar5iv ──────────────────────────────────────────

def _fetch_arxiv_figures(arxiv_id: str, max_figs: int = 6) -> list[dict[str, str]]:
    """Fetch figure image URLs + captions from ar5iv HTML.
    Returns list of {"url": ..., "caption": ...} dicts.
    """
    try:
        import urllib.request as _ur
        from urllib.parse import urljoin
        url = f"https://ar5iv.org/html/{arxiv_id}"
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with _ur.urlopen(req, timeout=10) as resp:
            final_url = resp.url  # capture post-redirect URL (e.g. ar5iv.labs.arxiv.org)
            html = resp.read().decode("utf-8", errors="ignore")

        # Extract <figure>...</figure> blocks
        figure_blocks = re.findall(r'<figure[^>]*>(.*?)</figure>', html, re.DOTALL)
        result: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for block in figure_blocks:
            # Get img src
            img_m = re.search(r'<img[^>]+src=["\']([^"\'#?][^"\']*)["\']', block)
            if not img_m:
                continue
            src = img_m.group(1)
            if src.startswith("data:"):
                continue
            if not src.startswith("http"):
                src = urljoin(final_url + "/", src.lstrip("/"))
            if src in seen_urls:
                continue
            seen_urls.add(src)

            # Get figcaption text (strip HTML tags)
            cap_m = re.search(r'<figcaption[^>]*>(.*?)</figcaption>', block, re.DOTALL)
            caption = ""
            if cap_m:
                caption = re.sub(r'<[^>]+>', ' ', cap_m.group(1))
                caption = re.sub(r'\s+', ' ', caption).strip()
                caption = caption[:200]  # truncate long captions

            result.append({"url": src, "caption": caption})
            if len(result) >= max_figs:
                break

        return result
    except Exception:
        return []


def _fetch_page_figures(page_url: str, max_figs: int = 4) -> list[dict[str, str]]:
    """Best-effort figure extraction from generic paper HTML pages."""
    try:
        import urllib.request as _ur
        from urllib.parse import urljoin

        req = _ur.Request(page_url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with _ur.urlopen(req, timeout=10) as resp:
            final_url = resp.url
            html = resp.read().decode("utf-8", errors="ignore")

        out: list[dict[str, str]] = []
        seen: set[str] = set()

        for block in re.findall(r'(?is)<figure[^>]*>.*?</figure>', html):
            img_m = re.search(r'(?is)<img[^>]+src=["\']([^"\']+)["\']', block)
            if not img_m:
                continue
            src = img_m.group(1).strip()
            if src.startswith("data:"):
                continue
            if not src.startswith("http"):
                src = urljoin(final_url, src)
            if src in seen:
                continue
            seen.add(src)
            cap = ""
            cap_m = re.search(r'(?is)<figcaption[^>]*>(.*?)</figcaption>', block)
            if cap_m:
                cap = re.sub(r"<[^>]+>", " ", cap_m.group(1))
                cap = re.sub(r"\s+", " ", cap).strip()[:180]
            out.append({"url": src, "caption": cap})
            if len(out) >= max_figs:
                return out

        # Fallback: og/twitter image
        for patt in (
            r'(?is)<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'(?is)<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            m = re.search(patt, html)
            if m:
                src = m.group(1).strip()
                if src:
                    if not src.startswith("http"):
                        src = urljoin(final_url, src)
                    if src not in seen:
                        out.append({"url": src, "caption": "Figure"})
                break
        return out[:max_figs]
    except Exception:
        return []


# ── Deep Markdown generation ───────────────────────────────────────────────

def _generate_deep_md(
    card: dict[str, Any],
    report: dict[str, Any],
    similar: list[dict[str, Any]],
    api_key: str,
    model: str,
) -> str:
    """Generate a rich long-form Markdown literature review for one paper."""
    title = card.get("title", "Untitled")
    venue = card.get("venue", "")
    date = card.get("date", "")
    abstract = card.get("source_abstract", "")
    link = card.get("link", "") or _best_link(card)
    paper_id = card.get("paper_id", "")
    scores = card.get("scores") or {}
    full_text = (card.get("source_content", "") or "")[:40000]

    md_body = ""
    ai_error = ""

    required_sections = [
        "AI Summary",
        "Abstract",
        "Method Details",
        "Summary",
        "Future Direction",
        "Pros and Cons",
    ]
    min_len = {
        "AI Summary": 100,
        "Abstract": 180,
        "Method Details": 420,
        "Summary": 120,
        "Future Direction": 100,
        "Pros and Cons": 140,
    }

    def _strip_md(s: str) -> str:
        s = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", s)
        s = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", s)
        s = re.sub(r"`{1,3}.*?`{1,3}", " ", s, flags=re.DOTALL)
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"[*_>#-]", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    def _normalize_math_delimiters(text: str) -> str:
        # Convert common LaTeX delimiters to Markdown math delimiters for KaTeX.
        text = re.sub(r"\\\((.+?)\\\)", r"$\1$", text, flags=re.DOTALL)
        text = re.sub(r"\\\[(.+?)\\\]", r"$$\1$$", text, flags=re.DOTALL)
        return text

    def _is_deep_enough(text: str) -> tuple[bool, str]:
        if not text.strip():
            return False, "empty output"
        probe = text
        first_line = probe.splitlines()[0] if probe.splitlines() else ""
        if first_line.startswith("TAGS:"):
            probe = probe[len(first_line):].lstrip("\n")

        blocks = re.findall(r"(?ms)^##\s+(.+?)\n(.*?)(?=^##\s+|\Z)", probe)
        by_title: dict[str, str] = {}
        for title_raw, content in blocks:
            title = re.sub(r"[^\w\s&]", "", title_raw).strip().lower()
            by_title[title] = content

        for sec in required_sections:
            key = sec.lower()
            if key not in by_title:
                return False, f"missing section: {sec}"
            pure = _strip_md(by_title[key])
            if len(pure) < min_len[sec]:
                return False, f"section too short: {sec} ({len(pure)} chars)"

        # Require explicit pros/cons subheadings
        pc = by_title.get("pros and cons", "")
        if "### Pros" not in pc or "### Cons" not in pc:
            return False, "missing Pros/Cons subsections"
        return True, ""

    # Pre-fetch figures so AI can embed them inline
    figures_data: list[dict[str, str]] = []
    if paper_id.startswith("arxiv:"):
        figures_data = _fetch_arxiv_figures(paper_id[6:], max_figs=6)
    elif link:
        figures_data = _fetch_page_figures(link, max_figs=4)

    if api_key.strip():
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key.strip())

            # Build figure reference block for the prompt
            if figures_data:
                fig_ref_lines = []
                for i, f in enumerate(figures_data):
                    cap = f["caption"] or f"Figure {i+1}"
                    fig_ref_lines.append(f"Figure {i+1}: {cap}\nURL: {f['url']}")
                fig_instructions = (
                    "\n\nAVAILABLE FIGURES (embed inline using Markdown image syntax):\n"
                    + "\n\n".join(fig_ref_lines)
                    + "\n\nINSTRUCTION: Embed only relevant figures INLINE inside the content sections "
                    "(especially Method Details and Summary) using:\n"
                    "![Figure N: caption](url)\n"
                    "Place each figure immediately after the paragraph that discusses it. "
                    "Do NOT create a standalone figures-only section.\n"
                )
            else:
                fig_instructions = ""

            sys_prompt = (
                "You are a senior researcher writing a comprehensive critical review of a paper. "
                "Write ENTIRELY in English. Help readers understand quickly; avoid unnecessary complexity.\n\n"
                "FIRST LINE: Output exactly one line in this format (choose 2-4 descriptive tags):\n"
                "TAGS: tag1, tag2, tag3\n"
                "Then output the review body (no YAML, no title line). Use ## for sections, ### for sub-sections.\n\n"
                "REQUIRED SECTIONS — follow this exact order and section titles exactly, skip none:\n\n"
                "## AI Summary\n"
                "1 concise paragraph with the central idea, key result, and practical takeaway.\n\n"
                "## Abstract\n"
                "Rewrite the paper abstract in clearer language (1-2 paragraphs, factual, no hype).\n\n"
                "## Method Details\n"
                "This is the key section for understanding (about 180-260 words, clear and practical).\n"
                "### Overall Framework\n"
                "Explain the core pipeline and key technical idea in plain language.\n"
                "### Technical Components\n"
                "Describe important modules, objectives/losses, and training setup (only what is essential).\n"
                "Include compact math only when it helps understanding. Render equations with LaTeX:\n"
                "- Inline equations: $...$\n"
                "- Display equations: $$...$$\n"
                "Never output raw LaTeX without $ delimiters.\n"
                "### Data and Experimental Setup\n"
                "Datasets, split, key metrics, and strongest baseline comparisons.\n"
                "Embed relevant figures directly in this section when available.\n\n"
                "## Summary\n"
                "Keep this short (80-150 words): what we learned and why it matters.\n\n"
                "## Future Direction\n"
                "List concrete future work directions (2-4 bullets) and why each matters.\n\n"
                "## Pros and Cons\n"
                "Use two subsections with bullet points:\n"
                "### Pros\n"
                "### Cons\n\n"
                "DO NOT output any section for related work/articles; it will be appended separately.\n"
                + fig_instructions
            )

            user_content = json.dumps({
                "title": title,
                "venue": venue,
                "date": date,
                "abstract": abstract,
                "prior_analysis": {
                    "methods": report.get("methods_detailed", ""),
                    "conclusion": report.get("main_conclusion", ""),
                    "future": report.get("future_direction", ""),
                    "value": report.get("value_assessment", ""),
                    "summary": report.get("ai_feed_summary", ""),
                },
                "full_text": full_text,
            }, ensure_ascii=False)

            primary_model = model.strip() or "gpt-4.1"
            model_chain = [primary_model, "gpt-4.1-mini"]
            # Deduplicate while preserving order
            model_chain = list(dict.fromkeys(model_chain))

            for m in model_chain:
                resp = client.responses.create(
                    model=m,
                    input=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    max_output_tokens=8192,
                    timeout=25,
                )
                cand = _normalize_math_delimiters((getattr(resp, "output_text", "") or "").strip())
                ok, reason = _is_deep_enough(cand)
                if ok:
                    md_body = cand
                    ai_error = ""
                    break
                ai_error = f"incomplete AI output ({m}): {reason}"
        except Exception as exc:
            ai_error = str(exc)
    else:
        ai_error = "Missing OpenAI API key"

    # Parse TAGS line from first line of AI output
    tags: list[str] = []
    if md_body:
        first_line = md_body.splitlines()[0]
        if first_line.startswith("TAGS:"):
            raw_tags = first_line[5:].strip()
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            md_body = md_body[len(first_line):].lstrip("\n")
    md_body = _normalize_math_delimiters(md_body)
    # If AI output omitted images but we found figures, inline a couple under Method Details.
    if figures_data and "![" not in md_body:
        fig_lines = []
        for i, f in enumerate(figures_data[:2], start=1):
            cap = f.get("caption") or f"Figure {i}"
            fig_lines.append(f"![Figure {i}: {cap}]({f['url']})")
        fig_block = "\n\n".join(fig_lines)
        md_body = re.sub(
            r"(?ms)(##\s+Method Details[^\n]*\n)",
            r"\1\n" + fig_block + "\n\n",
            md_body,
            count=1,
        )

    # Fallback: render from existing report fields
    if not md_body:
        parts = []
        if ai_error:
            parts.append(f"> ⚠️ AI deep generation failed, fallback content is shown. Error: `{ai_error}`")
        if report.get("ai_feed_summary"):
            parts.append(f"## AI Summary\n\n{report['ai_feed_summary']}")
        parts.append(f"## Abstract\n\n{abstract}")
        method_text = report.get("methods_detailed", "") or (
            "AI deep generation did not return sufficient methodological detail. "
            "Please regenerate this report after confirming model/key availability."
        )
        if figures_data:
            fig_lines = []
            for i, f in enumerate(figures_data[:2], start=1):
                cap = f.get("caption") or f"Figure {i}"
                fig_lines.append(f"![Figure {i}: {cap}]({f['url']})")
            method_text = method_text + "\n\n" + "\n\n".join(fig_lines)
        parts.append(f"## Method Details\n\n{method_text}")
        if report.get("main_conclusion"):
            parts.append(f"## Summary\n\n{report['main_conclusion']}")
        if report.get("future_direction"):
            parts.append(f"## Future Direction\n\n{report['future_direction']}")
        val = report.get("value_assessment", "")
        pros = "- Strengths are promising but not explicitly extracted in fallback mode."
        cons = "- Weaknesses need manual review if AI value assessment is unavailable."
        if val:
            pros = f"- {val}"
            cons = "- Requires deeper critical comparison with stronger baselines."
        parts.append(f"## Pros and Cons\n\n### Pros\n\n{pros}\n\n### Cons\n\n{cons}")
        md_body = "\n\n".join(parts) if parts else f"## AI Summary\n\nN/A\n\n## Abstract\n\n{abstract}"

    # Score badges + short reasons
    score_lines = []
    reasons = scores.get("reasons") if isinstance(scores, dict) else None
    reasons = reasons if isinstance(reasons, dict) else {}
    for k in ("relevance", "novelty", "rigor", "impact"):
        v = scores.get(k) if isinstance(scores, dict) else None
        if isinstance(v, (int, float)):
            iv = int(round(float(v)))
            bar = "█" * int(iv / 10) + "░" * (10 - int(iv / 10))
            reason = str(reasons.get(k, "")).strip() or "-"
            score_lines.append(f"| {k.capitalize()} | {bar} | {iv}/100 | {reason} |")
    score_table = ""
    if score_lines:
        score_table = (
            "\n| Dimension | Score | Value | Why |\n"
            "|-----------|-------|-------|-----|\n"
            + "\n".join(score_lines) + "\n"
        )

    # Related papers — prefer previously summarized internal reports
    related_section = ""
    if similar:
        items = []
        for s in similar:
            sim_title = s.get("title", "Unknown")
            sim_score = s.get("score", 0)
            sim_venue = s.get("venue", "")
            sim_date = s.get("date", "")
            sim_summary = (s.get("summary", "") or "").strip()
            app_url = _find_paper_report_url(s.get("paper_id", ""), sim_title)
            if app_url:
                linked = f"[{sim_title}]({app_url}) 📖 summarized"
            else:
                ext = _best_link(s)
                linked = f"[{sim_title}]({ext})" if ext else sim_title
            line = f"- **{sim_score:.2f}** · {linked} — *{sim_venue}* · {sim_date}"
            if sim_summary:
                line += f"\n  - Summary: {sim_summary}"
            items.append(line)
        related_section = "\n## Related Articles (Previously Summarized)\n\n" + "\n".join(items) + "\n"

    # YAML frontmatter
    tags_joined = ", ".join('"' + t + '"' for t in tags)
    tags_yaml = f"tags: [{tags_joined}]\n" if tags else ""
    frontmatter = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f'journal: "{venue}"\n'
        f'date: "{date}"\n'
        f'link: "{link}"\n'
        f'paper_id: "{paper_id}"\n'
        + tags_yaml
        + "---\n\n"
    )

    md = (
        frontmatter
        + f"# {title}\n\n"
        + f"> **{venue}** · {date}"
        + (f" · [Full Text →]({link})" if link else "")
        + "\n"
        + score_table
        + "\n\n"
        + md_body
        + related_section
    )

    return md


def _save_reports(
    date_str: str,
    report_cards: list[dict[str, Any]],
    also_notable: list[dict[str, Any]],
    api_key: str,
    model: str,
) -> Path:
    """Write per-paper .md files and a digest.md to reports/{date}/."""
    day_dir = REPORTS_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    paper_files: list[tuple[dict, Path]] = []
    for rc in report_cards:
        md = _generate_deep_md(
            rc, rc.get("report") or {}, rc.get("similar") or [], api_key, model
        )
        slug = _safe_slug(rc.get("title", "paper"))
        fpath = day_dir / f"{slug}.md"
        fpath.write_text(md, encoding="utf-8")
        paper_files.append((rc, fpath))

    # Group also_notable by venue
    by_venue: dict[str, list[dict]] = {}
    for c in also_notable:
        v = c.get("venue", "Other")
        by_venue.setdefault(v, []).append(c)

    # Write digest.md (English)
    lines: list[str] = [
        f"# 📚 Research Digest | {date_str}\n\n",
        f"**{len(report_cards) + len(also_notable)}** papers fetched · "
        f"**{len(report_cards)}** deep reads · "
        f"**{len(also_notable)}** also notable\n\n",
        "---\n\n",
        "## Deep Reads\n\n",
    ]
    for rc, fpath in paper_files:
        title = rc.get("title", "")
        venue = rc.get("venue", "")
        date = rc.get("date", "")
        link = rc.get("link", "")
        summary = (rc.get("report") or {}).get("ai_feed_summary", "") or rc.get("ai_feed_summary", "")
        slug = fpath.stem
        lines.append(f"### [{title}]({link})\n\n")
        lines.append(f"> **{venue}** | {date}\n\n")
        if summary:
            lines.append(f"{summary}\n\n")
        lines.append(f"[📖 Full Report](./{fpath.name})\n\n---\n\n")

    if also_notable:
        lines.append("## Also Notable\n\n")
        for venue_name, cards in sorted(by_venue.items()):
            lines.append(f"### {venue_name}\n\n")
            for c in cards:
                t = c.get("title", "")
                lnk = c.get("link", "")
                summary_short = (c.get("ai_feed_summary") or c.get("value_assessment") or "")[:120]
                bullet = f"- [{t}]({lnk})" if lnk else f"- {t}"
                if summary_short:
                    bullet += f" — {summary_short}"
                lines.append(bullet + "\n")
            lines.append("\n")

    digest_path = day_dir / "digest.md"
    digest_path.write_text("".join(lines), encoding="utf-8")

    return day_dir


# ── Pydantic models ────────────────────────────────────────────────────────

class Settings(BaseModel):
    language: str = "en"
    journals: list[str] = []
    custom_journals: list[str] = []
    fields: list[str] = []
    openai_api_key: str = ""
    api_model: str = "gpt-4.1-mini"
    max_reports: int = 5
    date_days: int = 3
    strict_journal: bool = True
    exclude_keywords: str = ""
    webhook_url: str = ""
    archive_db: str = ""


class RunPipelineRequest(BaseModel):
    settings: Settings
    force: bool = False  # override if today's run already exists


class SummarizeRequest(BaseModel):
    date: str
    card: dict[str, Any]
    settings: Settings


class NoteRequest(BaseModel):
    date: str
    card: dict[str, Any]
    report: dict[str, Any] = {}
    similar: list[dict[str, Any]] = []
    settings: Settings


class SaveReportRequest(BaseModel):
    content: str


# ── Note generation ────────────────────────────────────────────────────────

def _generate_note(
    card: dict[str, Any],
    report: dict[str, Any],
    similar: list[dict[str, Any]],
    api_key: str,
    model: str,
) -> str:
    """Generate a concise structured Obsidian-style reference note."""
    title = card.get("title", "Untitled")
    venue = card.get("venue", "")
    date = card.get("date", "")
    abstract = card.get("source_abstract", "")
    link = card.get("link", "") or _best_link(card)
    paper_id = card.get("paper_id", "")

    note_body = ""

    if api_key.strip():
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key.strip())

            sys_prompt = (
                "You are a research reading assistant. Based on the paper information provided, "
                "generate a concise structured reading note in Markdown format. Output the body directly, no ``` wrapping.\n\n"
                "Include the following sections (each ≤150 words):\n\n"
                "## One-Line Summary\n(≤20 words — capture the single most important contribution)\n\n"
                "## Core Contributions\n(3–5 bullet points, one sentence each)\n\n"
                "## Key Methods\n(step-by-step: key techniques and modules with specific details)\n\n"
                "## Datasets & Experiments\n(dataset names, scale, main metrics and findings)\n\n"
                "## Reflections\n(significance to the field + 1–2 open questions worth pursuing)"
            )

            resp = client.responses.create(
                model=model or "gpt-4.1-mini",
                input=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps({
                        "title": title, "venue": venue, "date": date,
                        "abstract": abstract,
                        "methods": report.get("methods_detailed", ""),
                        "conclusion": report.get("main_conclusion", ""),
                        "full_text": (card.get("source_content", "") or "")[:15000],
                    }, ensure_ascii=False)},
                ],
                max_output_tokens=1200,
            )
            note_body = (getattr(resp, "output_text", "") or "").strip()
        except Exception:
            pass

    # Fallback
    if not note_body:
        parts = []
        if report.get("ai_feed_summary"):
            parts.append(f"## Summary\n\n{report['ai_feed_summary']}")
        if report.get("methods_detailed"):
            parts.append(f"## Methods\n\n{report['methods_detailed']}")
        if report.get("main_conclusion"):
            parts.append(f"## Main Results\n\n{report['main_conclusion']}")
        note_body = "\n\n".join(parts) or f"## Abstract\n\n{abstract}"

    # Metadata table
    arxiv_id = paper_id[6:] if paper_id.startswith("arxiv:") else ""
    doi = paper_id[4:] if paper_id.startswith("doi:") else ""
    pmid = paper_id[5:] if paper_id.startswith("pmid:") else ""

    meta_rows = [f"| Venue | {venue} |", f"| Date | {date} |"]
    if link:
        meta_rows.append(f"| Full Text | [Link]({link}) |")
    if arxiv_id:
        meta_rows.append(f"| ar5iv | [Figures preview](https://ar5iv.org/abs/{arxiv_id}) |")
    if doi:
        meta_rows.append(f"| DOI | [{doi}](https://doi.org/{doi}) |")
    if pmid:
        meta_rows.append(f"| PubMed | [{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/) |")

    meta_table = (
        "## Metadata\n\n"
        "| Field | Value |\n|-------|-------|\n"
        + "\n".join(meta_rows) + "\n"
    )

    # Related — prefer app-internal links
    related = ""
    if similar:
        links = []
        for s in similar[:5]:
            st = s.get("title", "")
            sc = s.get("score", 0)
            app_url = _find_paper_report_url(s.get("paper_id", ""), st)
            if app_url:
                links.append(f"- [{st}]({app_url}) *(sim {sc:.0%}, 📖 internal report)*")
            else:
                sl = _best_link(s)
                links.append(f"- [{st}]({sl}) *(sim {sc:.0%})*" if sl else f"- {st} *(sim {sc:.0%})*")
        related = "\n## 🔗 Related Papers\n\n" + "\n".join(links) + "\n"

    frontmatter = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f'venue: "{venue}"\n'
        f'date: "{date}"\n'
        f'link: "{link}"\n'
        f'paper_id: "{paper_id}"\n'
        "type: paper-note\n"
        "---\n\n"
    )

    reading_space = (
        "\n---\n\n"
        "## 📝 Reading Notes\n\n"
        "> Write your thoughts, questions, or follow-up ideas here…\n\n"
        "&nbsp;\n"
    )

    return (
        frontmatter
        + f"# {title}\n\n"
        + meta_table
        + "\n"
        + note_body
        + related
        + reading_space
    )


# ── Settings endpoints ─────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    cfg = _load_config()
    return {
        "language": cfg.get("language", "en"),
        "journals": cfg.get("journals", []),
        "custom_journals": cfg.get("custom_journals", []),
        "fields": cfg.get("fields", []),
        "openai_api_key": cfg.get("openai_api_key", os.getenv("OPENAI_API_KEY", "")),
        "api_model": cfg.get("api_model", "gpt-4.1-mini"),
        "max_reports": cfg.get("max_reports", 5),
        "date_days": cfg.get("date_days", 3),
        "strict_journal": cfg.get("strict_journal", True),
        "exclude_keywords": cfg.get("exclude_keywords", ""),
        "webhook_url": cfg.get("webhook_url", ""),
        "archive_db": cfg.get("archive_db", DEFAULT_ARCHIVE),
        "journal_options": list(JOURNAL_OPTIONS),
        "field_options": list(FIELD_OPTIONS),
    }


@app.put("/api/settings")
def save_settings(body: Settings) -> dict[str, Any]:
    cfg = _load_config()
    cfg.update(body.model_dump())
    if not cfg.get("archive_db"):
        cfg["archive_db"] = DEFAULT_ARCHIVE
    _save_config(cfg)
    return {"ok": True}


# ── Runs endpoints ─────────────────────────────────────────────────────────

@app.get("/api/runs")
def list_all_runs() -> list[dict[str, Any]]:
    cfg = _load_config()
    db = cfg.get("archive_db", DEFAULT_ARCHIVE)
    return list_runs(db)


@app.get("/api/runs/{run_date}")
def get_run_data(run_date: str) -> dict[str, Any]:
    cfg = _load_config()
    db = cfg.get("archive_db", DEFAULT_ARCHIVE)
    run = get_run(db, run_date)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["report_cards"] = _enrich_links(run.get("report_cards", []))
    run["also_notable"] = _enrich_links(run.get("also_notable", []))
    return run


# ── Reports (Markdown files) endpoints ────────────────────────────────────

@app.delete("/api/runs/{run_date}")
def delete_run(run_date: str) -> dict[str, Any]:
    """Delete a run from the archive database."""
    import sqlite3
    cfg = _load_config()
    db = cfg.get("archive_db", DEFAULT_ARCHIVE)
    try:
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM runs WHERE run_date = ?", (run_date,))
            conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@app.delete("/api/reports/{date}")
def delete_report_date(date: str) -> dict[str, Any]:
    """Delete all report files for a given date."""
    import shutil
    day_dir = REPORTS_DIR / date
    if day_dir.exists():
        shutil.rmtree(str(day_dir))
    return {"ok": True}


@app.get("/api/reports")
def list_report_dates() -> list[dict[str, Any]]:
    if not REPORTS_DIR.exists():
        return []
    dates = []
    for d in sorted(REPORTS_DIR.iterdir(), reverse=True):
        if d.is_dir():
            md_files = list(d.glob("*.md"))
            if md_files:
                dates.append({"date": d.name, "files": len(md_files)})
    return dates


@app.get("/api/reports/{date}")
def list_report_files(date: str) -> dict[str, Any]:
    day_dir = REPORTS_DIR / date
    if not day_dir.exists():
        raise HTTPException(status_code=404, detail="No reports for this date")
    files = [
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(day_dir.glob("*.md"))
    ]
    return {"date": date, "path": str(day_dir), "files": files}


@app.get("/api/reports/{date}/{filename}")
def get_report_file(date: str, filename: str) -> dict[str, Any]:
    if not filename.endswith(".md") or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = REPORTS_DIR / date / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "date": date,
        "filename": filename,
        "path": str(fpath),
        "content": fpath.read_text("utf-8"),
    }


@app.put("/api/reports/{date}/{filename}")
def save_report_file(date: str, filename: str, body: SaveReportRequest) -> dict[str, Any]:
    """Save edited markdown content back to disk."""
    if not filename.endswith(".md") or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = REPORTS_DIR / date / filename
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": str(fpath)}


@app.delete("/api/reports/{date}/{filename}")
def delete_report_file(date: str, filename: str) -> dict[str, Any]:
    """Delete a single report .md file and clear report_json in DB (removes from network)."""
    if not filename.endswith(".md") or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = REPORTS_DIR / date / filename
    paper_id: str | None = None
    if fpath.exists():
        # Parse paper_id from YAML frontmatter before deleting
        try:
            content = fpath.read_text("utf-8")
            m = re.search(r'^paper_id:\s*"?([^"\n]+)"?', content, re.MULTILINE)
            if m:
                paper_id = m.group(1).strip()
        except Exception:
            pass
        fpath.unlink()
    # Clear report_json in DB so paper disappears from network (summarized_only filter)
    if paper_id:
        try:
            cfg = _load_config()
            db = cfg.get("archive_db", DEFAULT_ARCHIVE)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE papers SET report_json = NULL WHERE paper_id = ?",
                    (paper_id,),
                )
                conn.commit()
        except Exception:
            pass  # DB update is best-effort
    return {"ok": True}


# ── User notes endpoints ──────────────────────────────────────────────────

def _load_notes_meta() -> dict[str, Any]:
    if NOTES_META_FILE.exists():
        try:
            return json.loads(NOTES_META_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _save_notes_meta(meta: dict[str, Any]) -> None:
    NOTES_META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")


@app.get("/api/notes")
def list_notes() -> list[dict[str, Any]]:
    """List all user note files, extracting title from first # heading."""
    if not NOTES_DIR.exists():
        return []
    meta = _load_notes_meta()
    notes = []
    for f in sorted(NOTES_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        slug = f.stem
        name = slug.replace("_", " ")
        tags: list[str] = []
        try:
            text = f.read_text("utf-8")
            lines = text.splitlines()
            # Parse YAML frontmatter for tags
            if lines and lines[0].strip() == "---":
                for line in lines[1:]:
                    if line.strip() == "---":
                        break
                    if line.startswith("tags:"):
                        raw = line[5:].strip().strip("[]")
                        tags = [t.strip().strip('"').strip("'") for t in raw.split(",") if t.strip()]
            # Extract title from first # heading
            for line in lines:
                line = line.strip()
                if line.startswith("# "):
                    name = line[2:].strip()
                    break
        except Exception:
            pass
        slug_meta = meta.get(slug, {})
        notes.append({
            "slug": slug,
            "name": name,
            "tags": tags,
            "folder": slug_meta.get("folder", ""),
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        })
    return notes


@app.get("/api/notes-meta")
def get_notes_meta() -> dict[str, Any]:
    return _load_notes_meta()


class NoteMetaPatchRequest(BaseModel):
    slug: str
    folder: str = ""


@app.patch("/api/notes-meta")
def patch_note_meta(body: NoteMetaPatchRequest) -> dict[str, Any]:
    meta = _load_notes_meta()
    if body.slug not in meta:
        meta[body.slug] = {}
    meta[body.slug]["folder"] = body.folder
    _save_notes_meta(meta)
    return {"ok": True}


class FolderRenameRequest(BaseModel):
    old_name: str
    new_name: str


@app.post("/api/folders/rename")
def rename_folder(body: FolderRenameRequest) -> dict[str, Any]:
    meta = _load_notes_meta()
    for slug_data in meta.values():
        if slug_data.get("folder") == body.old_name:
            slug_data["folder"] = body.new_name
    _save_notes_meta(meta)
    return {"ok": True}


@app.get("/api/notes/{slug}")
def get_note(slug: str) -> dict[str, Any]:
    if ".." in slug:
        raise HTTPException(status_code=400, detail="Invalid slug")
    fpath = NOTES_DIR / f"{slug}.md"
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    return {
        "slug": slug,
        "name": slug.replace("_", " "),
        "content": fpath.read_text("utf-8"),
        "modified": fpath.stat().st_mtime,
    }


class NoteWriteRequest(BaseModel):
    content: str
    name: str = ""


@app.put("/api/notes/{slug}")
def save_note(slug: str, body: NoteWriteRequest) -> dict[str, Any]:
    if ".." in slug or not re.match(r'^[\w\-]+$', slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    fpath = NOTES_DIR / f"{slug}.md"
    fpath.write_text(body.content, encoding="utf-8")
    return {"ok": True, "slug": slug, "path": str(fpath)}


@app.delete("/api/notes/{slug}")
def delete_note(slug: str) -> dict[str, Any]:
    if ".." in slug:
        raise HTTPException(status_code=400, detail="Invalid slug")
    fpath = NOTES_DIR / f"{slug}.md"
    if fpath.exists():
        fpath.unlink()
    return {"ok": True}


@app.post("/api/papers/note")
async def generate_note(body: NoteRequest) -> dict[str, Any]:
    """Generate a structured AI paper note, save to notes/, and link it from report."""
    api_key = body.settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    model = body.settings.api_model or "gpt-4.1-mini"
    card = dict(body.card)
    card["link"] = _best_link(card)

    note_md = _generate_note(card, body.report, body.similar, api_key, model)

    # Save note into NOTES_DIR and pin it under "AI Paper Notes" folder metadata.
    base_slug = _safe_slug(card.get("title", "paper"), max_len=40)
    date_tag = re.sub(r"[^0-9]", "", body.date) or datetime.now(UTC).strftime("%Y%m%d")
    note_slug = f"ai_paper_note_{date_tag}_{base_slug}"
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    note_path = NOTES_DIR / f"{note_slug}.md"
    note_path.write_text(note_md, encoding="utf-8")

    meta = _load_notes_meta()
    if note_slug not in meta:
        meta[note_slug] = {}
    meta[note_slug]["folder"] = "AI Paper Notes"
    _save_notes_meta(meta)

    # Add a small section with note link at the end of this report (idempotent).
    report_slug = _safe_slug(card.get("title", "paper"))
    report_path = REPORTS_DIR / body.date / f"{report_slug}.md"
    if report_path.exists():
        try:
            report_text = report_path.read_text("utf-8")
            marker = f"/notes/{note_slug}"
            if marker not in report_text:
                link_section = (
                    "\n\n## AI Paper Note\n\n"
                    f"- [Open AI note](/notes/{note_slug})\n"
                )
                report_path.write_text(report_text.rstrip() + link_section + "\n", encoding="utf-8")
        except Exception:
            pass

    return {
        "ok": True,
        "slug": note_slug,
        "filename": note_path.name,
        "path": str(note_path),
        "content": note_md,
    }


# ── On-demand paper summarize ──────────────────────────────────────────────

@app.post("/api/papers/summarize")
async def summarize_paper(body: SummarizeRequest) -> dict[str, Any]:
    """Generate a report for a single paper and add it to the run's report_cards."""
    s = body.settings
    card = dict(body.card)
    date_str = body.date
    api_key = s.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    model = s.api_model or "gpt-4.1-mini"

    cfg = _load_config()
    archive_db = s.archive_db or cfg.get("archive_db", DEFAULT_ARCHIVE)

    settings_dict: dict[str, Any] = {
        "language": (s.language or "en"),
        "keywords": "",
        "openai_api_key": api_key,
        "api_model": model,
    }

    # Generate structured report
    report: dict[str, Any] = {}
    try:
        from research_pipeline import _generate_report
        prefs_rt: dict[str, Any] = {"language": (s.language or "en"), "fields": s.fields}
        report = _generate_report(card, settings_dict, prefs_rt)
        card["report"] = report
    except Exception as exc:
        report = {}

    # Store paper in archive
    try:
        init_archive(archive_db)
        store_paper(
            archive_db,
            paper_id=card.get("paper_id", ""),
            title=card.get("title", ""),
            abstract=card.get("source_abstract", ""),
            venue=card.get("venue", ""),
            publication_date=card.get("date", ""),
            report=report,
        )
    except Exception:
        pass

    # Find similar papers
    similar: list[dict[str, Any]] = []
    try:
        similar = find_similar(
            archive_db,
            title=card.get("title", ""),
            abstract=card.get("source_abstract", ""),
            exclude_paper_id=card.get("paper_id", ""),
            limit=5,
        )
        card["similar"] = similar
    except Exception:
        pass

    card["link"] = _best_link(card)

    # Generate deep MD file
    md_path = ""
    try:
        day_dir = REPORTS_DIR / date_str
        day_dir.mkdir(parents=True, exist_ok=True)
        md = _generate_deep_md(card, report, similar, api_key, model)
        slug = _safe_slug(card.get("title", "paper"))
        fpath = day_dir / f"{slug}.md"
        fpath.write_text(md, encoding="utf-8")
        md_path = str(fpath)
    except Exception:
        pass

    # Promote paper from also_notable to report_cards in the stored run
    import sqlite3
    try:
        with sqlite3.connect(archive_db) as conn:
            row = conn.execute(
                "SELECT papers_json FROM runs WHERE run_date = ?", (date_str,)
            ).fetchone()
            if row:
                data = json.loads(row[0])
                report_cards: list[dict] = data.get("report_cards", [])
                also_notable: list[dict] = data.get("also_notable", [])

                pid = card.get("paper_id", "")
                # Remove from also_notable
                also_notable = [c for c in also_notable if c.get("paper_id") != pid]
                # Add to report_cards if not already there
                if not any(c.get("paper_id") == pid for c in report_cards):
                    report_cards.append(card)

                data["report_cards"] = report_cards
                data["also_notable"] = also_notable
                conn.execute(
                    "UPDATE runs SET papers_json = ? WHERE run_date = ?",
                    (json.dumps(data, ensure_ascii=False), date_str),
                )
                conn.commit()
    except Exception:
        pass

    return {"ok": True, "report": report, "md_path": md_path, "card": card}


# ── Network / similarity endpoints ────────────────────────────────────────

def _gaussian_weight(sim: float, sigma: float = 0.3) -> float:
    dist = 1.0 - sim
    return math.exp(-(dist ** 2) / (2 * sigma ** 2))


@app.get("/api/network")
def get_network(
    limit: int = 200,
    threshold: float = 0.25,
    summarized_only: bool = True,
) -> dict[str, Any]:
    """Return nodes + edges for the paper network (summarized papers only by default)."""
    import sqlite3

    cfg = _load_config()
    db = cfg.get("archive_db", DEFAULT_ARCHIVE)
    if not Path(db).exists():
        return {"nodes": [], "edges": []}

    _STOP = {
        "the","a","an","and","or","of","in","to","for","with","on","at","by",
        "from","is","are","was","were","be","been","this","that","these","those",
        "we","our","their","also","can","may","using","used","based","which",
        "as","its","it","not","but","has","have","had","do","does","did","all",
        "than","more","into","such","about","between","through","across","within",
        "over","under","show","shows","shown","method","approach","model","models",
        "data","dataset","result","results","propose","proposed","present","use",
        "new","large","high","low","each","both","than","study","studies",
        "performance","training","learning","task","tasks",
    }

    def _word_bag(text: str) -> set[str]:
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        return {w for w in words if w not in _STOP}

    try:
        with sqlite3.connect(db) as conn:
            # Check columns; add word_bag if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
            if "word_bag" not in cols:
                conn.execute("ALTER TABLE papers ADD COLUMN word_bag TEXT DEFAULT ''")
                conn.commit()

            # Check if abstract column exists
            has_abstract = "abstract" in cols

            if summarized_only:
                # Build set of paper_ids that have actual .md deep-report files
                deep_ids: set[str] = set()
                if REPORTS_DIR.exists():
                    for md_file in REPORTS_DIR.rglob("*.md"):
                        if md_file.name == "digest.md" or md_file.name.endswith("_note.md"):
                            continue
                        try:
                            text = md_file.read_text("utf-8", errors="ignore")
                            m = re.search(r'^paper_id:\s*"?([^"\n]+)"?', text, re.MULTILINE)
                            if m:
                                deep_ids.add(m.group(1).strip())
                        except Exception:
                            pass

                if not deep_ids:
                    return {"nodes": [], "edges": []}

                placeholders = ",".join("?" * len(deep_ids))
                q = (
                    f"SELECT paper_id, title, venue, publication_date, word_bag"
                    f"{', abstract' if has_abstract else ''} "
                    f"FROM papers WHERE paper_id IN ({placeholders}) "
                    "ORDER BY stored_at DESC LIMIT ?"
                )
                rows = conn.execute(q, (*deep_ids, limit)).fetchall()
            else:
                q = (
                    f"SELECT paper_id, title, venue, publication_date, word_bag"
                    f"{', abstract' if has_abstract else ''} "
                    "FROM papers ORDER BY stored_at DESC LIMIT ?"
                )
                rows = conn.execute(q, (limit,)).fetchall()
    except Exception:
        return {"nodes": [], "edges": []}

    if not rows:
        return {"nodes": [], "edges": []}

    try:
        nodes = []
        bags: list[set[str]] = []
        for row in rows:
            pid, title, venue, pub_date, wbag = row[0], row[1], row[2], row[3], row[4]
            abstract = row[5] if len(row) > 5 else ""
            link = _best_link({"paper_id": pid or ""})
            v_parts = (venue or "").split()
            nodes.append({
                "id": pid or title or "",
                "title": title or "",
                "venue": venue or "",
                "date": pub_date or "",
                "link": link,
                "group": v_parts[0] if v_parts else "Other",
            })
            # Use stored word_bag or compute from title+abstract
            if wbag and wbag.strip():
                bag = set(wbag.split())
            else:
                fallback = f"{title or ''} {abstract or ''}"
                bag = _word_bag(fallback)
            bags.append(bag)

        edges = []
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = bags[i], bags[j]
                if not a or not b:
                    continue
                union = a | b
                if not union:
                    continue
                jaccard = len(a & b) / len(union)
                if jaccard < threshold:
                    continue
                weight = _gaussian_weight(jaccard)
                edges.append({
                    "source": nodes[i]["id"],
                    "target": nodes[j]["id"],
                    "weight": round(weight, 4),
                    "similarity": round(jaccard, 4),
                })
    except Exception:
        return {"nodes": [], "edges": []}

    return {"nodes": nodes, "edges": edges}


# ── Pipeline execution (SSE) ───────────────────────────────────────────────

@app.get("/api/pipeline/status")
def get_pipeline_status() -> dict[str, Any]:
    """Return current pipeline task state (for polling)."""
    with _pipeline_lock:
        return dict(_pipeline_state)


@app.post("/api/pipeline/run")
def run_pipeline(body: RunPipelineRequest) -> dict[str, Any]:
    """Start pipeline in background thread. Returns immediately."""
    with _pipeline_lock:
        if _pipeline_state["status"] == "running":
            return {"started": False, "reason": "already_running"}

    # Check if today's run already exists (unless force=True)
    if not body.force:
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        _cfg = _load_config()
        _archive_db = body.settings.archive_db or _cfg.get("archive_db", DEFAULT_ARCHIVE)
        try:
            from paper_archive import get_run as _get_run
            if _get_run(_archive_db, today_str) is not None:
                return {"started": False, "reason": "already_run_today", "date": today_str}
        except Exception:
            pass  # DB not ready yet — proceed normally

    with _pipeline_lock:
        _pipeline_state.update({
            "status": "running",
            "logs": ["🚀 Starting pipeline…"],
            "date": None,
            "total": 0,
            "reports": 0,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        })

    s = body.settings
    cfg = _load_config()

    settings_dict: dict[str, Any] = {
        "language": (s.language or "en"),
        "keywords": "",
        "exclude_keywords": s.exclude_keywords,
        "journals": s.journals + s.custom_journals,
        "strict_journal_only": s.strict_journal,
        "push_schedule": "daily",
        "custom_days": s.date_days,
        "date_range_days": s.date_days,
        "openai_api_key": s.openai_api_key or cfg.get("openai_api_key", os.getenv("OPENAI_API_KEY", "")),
        "api_model": s.api_model,
        "enable_webhook_push": bool((s.webhook_url or "").strip()),
        "webhook_url": s.webhook_url,
    }
    archive_db = s.archive_db or cfg.get("archive_db", DEFAULT_ARCHIVE)
    api_key = settings_dict["openai_api_key"]
    model = s.api_model
    max_reports = s.max_reports

    def _run() -> None:
        try:
            from research_pipeline import (
                DEFAULT_SIMILAR_LIMIT,
                _build_slack_text,
                _generate_report,
            )
            from paper_archive import find_similar, init_archive, store_paper, store_run, archive_size
            from app import build_digest, build_runtime_prefs_from_settings, fetch_candidates
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from datetime import UTC, datetime

            _pipeline_log("📡 Fetching papers from RSS feeds…")

            prefs, _ = build_runtime_prefs_from_settings(settings_dict)
            prefs_rt = dict(prefs)
            papers, fetch_note, fetch_diag, eff = fetch_candidates(prefs_rt)
            prefs_rt["date_range_days"] = int(eff.get("effective_days", prefs_rt.get("date_range_days", 3)))
            prefs_rt["strict_journal_only"] = bool(eff.get("effective_strict_journal_only", True))
            lang = str(settings_dict.get("language", "en")).lower()
            days = int(eff.get("effective_days", prefs_rt.get("date_range_days", 3)))
            strict = bool(eff.get("effective_strict_journal_only", True))
            if lang == "zh":
                strict_txt = "开" if strict else "关"
                _pipeline_log(f"📄 抓取完成：窗口 {days} 天，严格期刊匹配={strict_txt}。")
            else:
                strict_txt = "on" if strict else "off"
                _pipeline_log(f"📄 Fetch complete: {days}-day window, strict journal matching={strict_txt}.")

            digest = build_digest(prefs_rt, papers)
            top_picks = digest.get("top_picks", [])
            also_notable = digest.get("also_notable", [])
            all_cards = list(top_picks) + list(also_notable)

            if not all_cards:
                _pipeline_log("⚠️ No papers matched filters today.")
                with _pipeline_lock:
                    _pipeline_state.update({"status": "done", "finished_at": time.time()})
                return

            _pipeline_log(f"✅ Found {len(all_cards)} papers ({len(top_picks)} deep reads). Generating reports…")

            init_archive(archive_db)
            prev_count = archive_size(archive_db)
            selected = all_cards[:max_reports]

            def _process(c: dict[str, Any]) -> dict[str, Any]:
                pid = c.get("paper_id", "")
                title = c.get("title", "")
                abstract = c.get("source_abstract", "")
                similar: list[dict[str, Any]] = []
                if prev_count > 0:
                    similar = find_similar(archive_db, title=title, abstract=abstract,
                                           exclude_paper_id=pid, limit=DEFAULT_SIMILAR_LIMIT)
                report = _generate_report(c, settings_dict, prefs_rt)
                store_paper(archive_db, paper_id=pid, title=title, abstract=abstract,
                            venue=c.get("venue", ""), publication_date=c.get("date", ""),
                            report=report)
                c["link"] = _best_link(c)
                return {**c, "report": report, "similar": similar}

            report_cards: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=min(4, len(selected))) as ex:
                futs = {ex.submit(_process, c): c for c in selected}
                for fut in as_completed(futs):
                    try:
                        rc = fut.result()
                        report_cards.append(rc)
                        _pipeline_log(f"✏️ Report ready: {rc.get('title', '')[:60]}…")
                    except Exception:
                        orig = futs[fut]
                        report_cards.append({**orig, "report": {}, "similar": [], "link": _best_link(orig)})

            order = {c.get("paper_id"): i for i, c in enumerate(selected)}
            report_cards.sort(key=lambda x: order.get(x.get("paper_id", ""), 999))

            report_pids = {rc.get("paper_id") for rc in report_cards}
            also_for_push = []
            for c in all_cards:
                c = dict(c)
                c["link"] = _best_link(c)
                if c.get("paper_id") not in report_pids:
                    store_paper(archive_db, paper_id=c.get("paper_id", ""),
                                title=c.get("title", ""), abstract=c.get("source_abstract", ""),
                                venue=c.get("venue", ""), publication_date=c.get("date", ""),
                                report={})
                    also_for_push.append(c)

            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            slack_text = _build_slack_text(
                date_str=date_str,
                report_cards=report_cards,
                also_notable=also_for_push,
                total_fetched=len(all_cards),
                lang="zh",
            )
            store_run(archive_db, date_str, report_cards, also_for_push, slack_text)

            _pipeline_log("📝 Saving markdown reports…")
            try:
                _save_reports(date_str, report_cards, also_for_push, api_key, model)
                _pipeline_log(f"📁 Reports saved to reports/{date_str}/ ({len(report_cards)} files)")
            except Exception as md_exc:
                _pipeline_log(f"⚠️ MD save failed: {md_exc}")

            _pipeline_log(f"💾 Done! {len(report_cards)} deep reports, {len(all_cards)} total papers.")
            with _pipeline_lock:
                _pipeline_state.update({
                    "status": "done",
                    "date": date_str,
                    "total": len(all_cards),
                    "reports": len(report_cards),
                    "finished_at": time.time(),
                })

        except Exception as exc:
            _pipeline_log(f"❌ Error: {exc}")
            with _pipeline_lock:
                _pipeline_state.update({
                    "status": "error",
                    "error": str(exc),
                    "finished_at": time.time(),
                })

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True}
