"""FastAPI backend for the Research Pipeline web app."""
from __future__ import annotations

import json
import math
import os
import re
import sys
import asyncio
import base64
import hashlib
import io
from pathlib import Path
from typing import Any, AsyncGenerator

import threading
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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


def _app_mode() -> str:
    cfg = _load_config()
    mode = str(cfg.get("app_mode", os.getenv("APP_MODE", "normal"))).strip().lower()
    return mode or "normal"


def _is_beta_mode() -> bool:
    return _app_mode() == "beta"


def _normalize_timezone(tz: str | None) -> str:
    candidate = str(tz or "").strip() or str(os.getenv("APP_TIMEZONE", "UTC")).strip() or "UTC"
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return "UTC"


def _default_timezone() -> str:
    cfg = _load_config()
    return _normalize_timezone(cfg.get("timezone", os.getenv("APP_TIMEZONE", "UTC")))


def _today_in_tz(tz: str | None = None) -> str:
    tz_name = _normalize_timezone(tz or _default_timezone())
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _beta_note_daily_limit() -> int:
    try:
        return max(1, int(os.getenv("BETA_DAILY_NOTE_LIMIT", "1")))
    except Exception:
        return 1


def _beta_forced_model() -> str:
    # Keep beta costs predictable; can be overridden by env if needed.
    return str(os.getenv("BETA_FORCED_MODEL", "gpt-4.1-mini")).strip() or "gpt-4.1-mini"


def _normalize_api_provider(provider: str | None) -> str:
    p = str(provider or "").strip().lower()
    return p if p in {"openai", "gemini"} else "gemini"


def _default_model_for_provider(provider: str | None) -> str:
    p = _normalize_api_provider(provider)
    return "gemini-2.5-flash-lite" if p == "gemini" else "gpt-4.1-mini"


def _clamp_max_reports(v: Any) -> int:
    try:
        n = int(v)
    except Exception:
        n = 5
    return max(1, min(5, n))


def _effective_model(requested: str | None, provider: str | None = None) -> str:
    if _is_beta_mode():
        return _beta_forced_model()
    m = str(requested or "").strip()
    return m or _default_model_for_provider(provider)


def _resolve_api_key(provider: str, settings_key: str, cfg_key: str, env_keys: list[str]) -> str:
    if settings_key.strip():
        return settings_key.strip()
    cfg = _load_config()
    cfg_value = str(cfg.get(cfg_key, "") or "").strip()
    if cfg_value:
        return cfg_value
    for ek in env_keys:
        v = str(os.getenv(ek, "") or "").strip()
        if v:
            return v
    return ""


def _resolve_provider_and_key(
    provider: str | None,
    openai_api_key: str | None,
    gemini_api_key: str | None,
) -> tuple[str, str]:
    p = _normalize_api_provider(provider)
    if p == "gemini":
        key = _resolve_api_key(
            provider=p,
            settings_key=str(gemini_api_key or ""),
            cfg_key="gemini_api_key",
            env_keys=["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        )
        return p, key
    key = _resolve_api_key(
        provider=p,
        settings_key=str(openai_api_key or ""),
        cfg_key="openai_api_key",
        env_keys=["OPENAI_API_KEY"],
    )
    return p, key


def _make_openai_compatible_client(provider: str, api_key: str):
    from openai import OpenAI
    if _normalize_api_provider(provider) == "gemini":
        base_url = str(
            os.getenv("GEMINI_OPENAI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
        ).strip()
        return OpenAI(api_key=api_key.strip(), base_url=base_url)
    return OpenAI(api_key=api_key.strip())


def _create_text_completion(
    client: Any,
    provider: str,
    model: str,
    system_prompt: str,
    user_content: str,
    max_output_tokens: int,
    timeout: int | None = None,
) -> str:
    p = _normalize_api_provider(provider)
    if p == "gemini":
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_output_tokens,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        resp = client.chat.completions.create(**kwargs)
        choice = (getattr(resp, "choices", None) or [None])[0]
        msg = getattr(choice, "message", None)
        content = getattr(msg, "content", "") if msg else ""
        return (content or "").strip()

    kwargs = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_output_tokens": max_output_tokens,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    resp = client.responses.create(**kwargs)
    return (getattr(resp, "output_text", "") or "").strip()


def _check_beta_note_limit_or_raise(tz: str | None = None) -> None:
    if not _is_beta_mode():
        return
    today = _today_in_tz(tz)
    cfg = _load_config()
    usage = cfg.get("beta_note_daily_usage", {})
    if not isinstance(usage, dict):
        usage = {}
    used = int(usage.get(today, 0) or 0)
    limit = _beta_note_daily_limit()
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Beta limit reached: generate note is limited to {limit} per day ({_normalize_timezone(tz)}).",
        )


def _mark_beta_note_usage(tz: str | None = None) -> None:
    if not _is_beta_mode():
        return
    today = _today_in_tz(tz)
    cfg = _load_config()
    usage = cfg.get("beta_note_daily_usage", {})
    if not isinstance(usage, dict):
        usage = {}
    usage[today] = int(usage.get(today, 0) or 0) + 1
    # keep recent history only
    keys = sorted(usage.keys(), reverse=True)
    usage = {k: usage[k] for k in keys[:14]}
    cfg["beta_note_daily_usage"] = usage
    _save_config(cfg)


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

        # Fallback: scan generic <img> tags (useful for publishers that do not wrap with <figure>)
        def _is_noise_image(src: str, alt: str) -> bool:
            s = (src or "").lower()
            a = (alt or "").lower()
            noise_keys = (
                "logo", "icon", "sprite", "avatar", "badge", "banner",
                "social", "share", "tracking", "pixel", "favicon", "nav",
            )
            if any(k in s for k in noise_keys):
                return True
            if any(k in a for k in ("logo", "icon", "navigation", "menu")):
                return True
            return False

        for m in re.finditer(r'(?is)<img\b([^>]+)>', html):
            tag = m.group(1)
            src_m = re.search(r'(?is)\bsrc=["\']([^"\']+)["\']', tag)
            if not src_m:
                continue
            src = src_m.group(1).strip()
            if not src or src.startswith("data:"):
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif not src.startswith("http"):
                src = urljoin(final_url, src)

            alt_m = re.search(r'(?is)\balt=["\']([^"\']*)["\']', tag)
            alt = (alt_m.group(1).strip() if alt_m else "")
            if _is_noise_image(src, alt):
                continue
            if src in seen:
                continue
            seen.add(src)

            cap = alt or "Figure"
            # Prefer likely scientific figures first
            priority = 0
            s_l = src.lower()
            if re.search(r"(?:/|_|-)(fig(?:ure)?|image|media)(?:/|_|-|\d)", s_l):
                priority += 2
            if re.search(r"\.(png|jpe?g|webp)(?:[\?#].*)?$", s_l):
                priority += 1

            out.append({"url": src, "caption": cap[:180], "_priority": str(priority)})

        if out:
            out.sort(key=lambda x: int(x.get("_priority", "0")), reverse=True)
            cleaned = [{"url": x["url"], "caption": x.get("caption", "")} for x in out[:max_figs]]
            return cleaned

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


def _candidate_pdf_urls(card: dict[str, Any]) -> list[str]:
    link = (card.get("link") or "").strip()
    pid = (card.get("paper_id") or "").strip()
    out: list[str] = []
    if not link and not pid:
        return out

    if link.lower().endswith(".pdf"):
        out.append(link)
    if pid.startswith("arxiv:"):
        out.append(f"https://arxiv.org/pdf/{pid[6:]}.pdf")

    # bioRxiv / medRxiv: links may include query params like ?rss=1
    if link and (re.search(r"biorxiv\.org/content/", link) or re.search(r"medrxiv\.org/content/", link)):
        base = link.split("?", 1)[0].rstrip("/")
        out.append(base + ".full.pdf")
        out.append(base + ".full.pdf?download=true")
        out.append(base + ".full.pdf?download=1")
        # fallback to raw with query stripped normalization
        out.append(link.rstrip("/") + ".full.pdf")

    # de-duplicate while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def _discover_pdf_urls_from_landing(page_url: str) -> list[str]:
    """Parse landing HTML for explicit PDF links (e.g., citation_pdf_url)."""
    if not page_url:
        return []
    try:
        import requests
        import certifi
        from requests.exceptions import SSLError as _ReqSSLError
        from urllib.parse import urljoin

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            resp = requests.get(page_url, headers=headers, timeout=15, allow_redirects=True, verify=certifi.where())
        except _ReqSSLError:
            resp = requests.get(page_url, headers=headers, timeout=15, allow_redirects=True, verify=False)
        if resp.status_code >= 400:
            return []
        html = resp.text or ""
        final_url = resp.url or page_url
        found: list[str] = []

        # <meta name="citation_pdf_url" content="...">
        for m in re.finditer(r'(?is)<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']', html):
            u = m.group(1).strip()
            if u:
                found.append(u if u.startswith("http") else urljoin(final_url, u))

        # Any direct href ending with .pdf
        for m in re.finditer(r'(?is)<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html):
            u = m.group(1).strip()
            if u:
                found.append(u if u.startswith("http") else urljoin(final_url, u))

        # Deduplicate preserving order
        seen: set[str] = set()
        out: list[str] = []
        for u in found:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out
    except Exception:
        return []


def _fetch_pdf_figures(pdf_url: str, max_figs: int = 2) -> list[dict[str, str]]:
    """Best-effort extraction of embedded images from PDF to data-URI figures."""
    if not pdf_url:
        return []
    try:
        import urllib.request as _ur

        req = _ur.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with _ur.urlopen(req, timeout=8) as resp:
            pdf_bytes = resp.read()
    except Exception:
        return []

    try:
        from pypdf import PdfReader
    except Exception:
        return []

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    max_pages = min(8, len(reader.pages))

    for page_idx in range(max_pages):
        page = reader.pages[page_idx]
        images = getattr(page, "images", None) or []
        for img in images:
            data = getattr(img, "data", b"") or b""
            name = str(getattr(img, "name", "") or "").lower()
            if not data:
                continue
            # Skip likely icons/sprites and overly large payloads for markdown
            if len(data) < 8_000 or len(data) > 900_000:
                continue
            if any(k in name for k in ("logo", "icon", "sprite", "favicon")):
                continue

            mime = "image/png"
            if name.endswith(".jpg") or name.endswith(".jpeg"):
                mime = "image/jpeg"
            elif name.endswith(".webp"):
                mime = "image/webp"

            b64 = base64.b64encode(data).decode("ascii")
            data_url = f"data:{mime};base64,{b64}"
            sig = data_url[:120]
            if sig in seen:
                continue
            seen.add(sig)
            out.append({
                "url": data_url,
                "caption": f"PDF Figure {len(out)+1} (page {page_idx+1})",
            })
            if len(out) >= max_figs:
                return out
    return out


def _extract_pdf_figures_from_bytes(pdf_bytes: bytes, max_figs: int = 2) -> list[dict[str, str]]:
    try:
        from pypdf import PdfReader
    except Exception:
        return []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    max_pages = min(8, len(reader.pages))

    for page_idx in range(max_pages):
        page = reader.pages[page_idx]
        images = getattr(page, "images", None) or []
        for img in images:
            data = getattr(img, "data", b"") or b""
            name = str(getattr(img, "name", "") or "").lower()
            if not data:
                continue
            if len(data) < 8_000 or len(data) > 900_000:
                continue
            if any(k in name for k in ("logo", "icon", "sprite", "favicon")):
                continue

            mime = "image/png"
            if name.endswith(".jpg") or name.endswith(".jpeg"):
                mime = "image/jpeg"
            elif name.endswith(".webp"):
                mime = "image/webp"

            b64 = base64.b64encode(data).decode("ascii")
            data_url = f"data:{mime};base64,{b64}"
            sig = data_url[:120]
            if sig in seen:
                continue
            seen.add(sig)
            out.append({
                "url": data_url,
                "caption": f"PDF Figure {len(out)+1} (page {page_idx+1})",
            })
            if len(out) >= max_figs:
                return out
    return out


def _extract_pdf_figures_from_file(pdf_file: Path, max_figs: int = 2) -> list[dict[str, str]]:
    try:
        if not pdf_file.exists():
            return []
        data = pdf_file.read_bytes()
        if not data.startswith(b"%PDF"):
            return []
        return _extract_pdf_figures_from_bytes(data, max_figs=max_figs)
    except Exception:
        return []


def _download_pdf_copy(pdf_url: str, dest_file: Path) -> tuple[bool, str]:
    """Download a PDF to local disk for report traceability."""
    if not pdf_url:
        return False, "empty url"
    try:
        import requests
        import certifi
        from requests.exceptions import SSLError as _ReqSSLError
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible)",
            "Accept": "application/pdf,*/*;q=0.8",
            "Referer": "https://www.biorxiv.org/",
        }

        try:
            resp = requests.get(
                pdf_url,
                headers=headers,
                timeout=20,
                allow_redirects=True,
                verify=certifi.where(),
            )
        except _ReqSSLError:
            # Fallback for local cert-store issues on some machines.
            resp = requests.get(
                pdf_url,
                headers=headers,
                timeout=20,
                allow_redirects=True,
                verify=False,
            )
        if resp.status_code >= 400:
            return False, f"http {resp.status_code}"
        data = resp.content or b""
        if not data or len(data) < 1_024:
            return False, "payload too small"
        # Minimal PDF signature check (allow small preamble before %PDF)
        head = data[:2048]
        pos = head.find(b"%PDF")
        if pos < 0:
            ctype = (resp.headers.get("content-type") or "").lower()
            return False, f"not a pdf payload (content-type={ctype or 'unknown'})"
        if pos > 0:
            data = data[pos:]
        dest_file.write_bytes(data)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _maybe_download_pdf_for_report(
    card: dict[str, Any],
    day_dir: Path,
    slug: str,
    log_cb: Any | None = None,
) -> tuple[str, str]:
    """Return (local_api_url, source_pdf_url) when local PDF copy exists."""
    candidates = _candidate_pdf_urls(card)
    link = (card.get("link") or "").strip()
    if link:
        candidates.extend(_discover_pdf_urls_from_landing(link))
        # de-dup again
        seen2: set[str] = set()
        uniq2: list[str] = []
        for u in candidates:
            if u and u not in seen2:
                seen2.add(u)
                uniq2.append(u)
        candidates = uniq2
    if not candidates:
        return "", ""
    pdf_src = candidates[0]
    assets_dir = day_dir / "assets"
    local_name = f"{slug}.pdf"
    local_file = assets_dir / local_name
    if not local_file.exists():
        ok = False
        for c in candidates:
            ok_c, reason = _download_pdf_copy(c, local_file)
            if ok_c:
                pdf_src = c
                ok = True
                break
            if callable(log_cb):
                short = reason[:180] if reason else "unknown"
                log_cb(f"⚠️ PDF download failed for {slug}: {short}")
        if not ok:
            return "", pdf_src
    return f"/api/reports/{day_dir.name}/assets/{local_name}", pdf_src


def _try_download_pdf_for_report(
    card: dict[str, Any],
    day_dir: Path,
    slug: str,
) -> tuple[bool, str, str]:
    """Return (ok, local_url, detail). detail is either chosen source URL or failure reason."""
    candidates = _candidate_pdf_urls(card)
    link = (card.get("link") or "").strip()
    if link:
        candidates.extend(_discover_pdf_urls_from_landing(link))
    seen: set[str] = set()
    uniq: list[str] = []
    for u in candidates:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    if not uniq:
        return False, "", "no candidate pdf url"

    assets_dir = day_dir / "assets"
    local_name = f"{slug}.pdf"
    local_file = assets_dir / local_name
    if local_file.exists():
        return True, f"/api/reports/{day_dir.name}/assets/{local_name}", "already cached"

    last_reason = "unknown"
    for u in uniq:
        ok, reason = _download_pdf_copy(u, local_file)
        if ok:
            return True, f"/api/reports/{day_dir.name}/assets/{local_name}", u
        last_reason = f"{u} -> {reason}"
    return False, "", last_reason


def _local_pdf_file_for_report(day_dir: Path, slug: str) -> Path:
    return day_dir / "assets" / f"{slug}.pdf"


def _extract_figures_from_text(text: str, max_figs: int = 4) -> list[dict[str, str]]:
    """Extract image URLs from already-fetched paper text (markdown/html snippets)."""
    if not text:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _looks_like_figure_url(u: str) -> bool:
        if re.search(r"\.(png|jpe?g|webp|gif|svg)(?:[\?#].*)?$", u, re.IGNORECASE):
            return True
        # Many publisher figure URLs do not end with an image extension.
        return bool(re.search(r"(?:/|_|-)(fig(?:ure)?|image|media)(?:/|_|-|\d)", u, re.IGNORECASE))

    def _push(url: str, caption: str = "") -> None:
        if len(out) >= max_figs:
            return
        u = (url or "").strip()
        if not u or u in seen:
            return
        if not _looks_like_figure_url(u):
            return
        seen.add(u)
        out.append({"url": u, "caption": (caption or "").strip()[:180]})

    # Markdown images: ![caption](url)
    for m in re.finditer(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", text, flags=re.IGNORECASE):
        _push(m.group(2), m.group(1))
        if len(out) >= max_figs:
            return out

    # HTML images: <img src="...">
    for m in re.finditer(r'(?is)<img[^>]+src=["\'](https?://[^"\']+)["\']', text):
        _push(m.group(1), "Figure")
        if len(out) >= max_figs:
            return out

    # Raw direct image URLs in text
    for m in re.finditer(r"https?://[^\s)\"']+\.(?:png|jpe?g|webp|gif|svg)(?:\?[^\s)\"']*)?", text, flags=re.IGNORECASE):
        _push(m.group(0), "Figure")
        if len(out) >= max_figs:
            return out

    return out


def _score_reason_is_generic(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    generic_prefixes = (
        "estimated from",
        "combined from",
        "based on keyword",
        "derived from",
    )
    return any(t.startswith(p) for p in generic_prefixes)


def _generate_ai_score_reasons(
    *,
    api_provider: str,
    api_key: str,
    model: str,
    title: str,
    venue: str,
    date: str,
    abstract: str,
    report: dict[str, Any],
    full_text: str,
    scores: dict[str, Any],
) -> dict[str, str]:
    if not api_key.strip():
        return {}
    try:
        client = _make_openai_compatible_client(api_provider, api_key)
        sys_prompt = (
            "You are a strict scientific reviewer. Explain 4 paper scores with concrete evidence.\n"
            "Return ONLY JSON with keys: relevance, novelty, rigor, impact.\n"
            "Each value should be 1-2 concise sentences (roughly 20-45 words), specific and non-generic.\n"
            "Use only available evidence; if uncertain, explicitly say uncertainty."
        )
        payload = {
            "title": title,
            "venue": venue,
            "date": date,
            "scores": {
                "relevance": scores.get("relevance"),
                "novelty": scores.get("novelty"),
                "rigor": scores.get("rigor"),
                "impact": scores.get("impact"),
            },
            "abstract": abstract,
            "report_summary": report.get("ai_feed_summary", ""),
            "methods": report.get("methods_detailed", ""),
            "conclusion": report.get("main_conclusion", ""),
            "future_direction": report.get("future_direction", ""),
            "paper_text_excerpt": (full_text or "")[:8000],
        }
        raw = _create_text_completion(
            client=client,
            provider=api_provider,
            model=model or _default_model_for_provider(api_provider),
            system_prompt=sys_prompt,
            user_content=json.dumps(payload, ensure_ascii=False),
            max_output_tokens=900,
            timeout=20,
        )
        s = raw.strip()
        if "{" in s and "}" in s:
            s = s[s.find("{"): s.rfind("}") + 1]
        obj = json.loads(s)
        out: dict[str, str] = {}
        for k in ("relevance", "novelty", "rigor", "impact"):
            v = str(obj.get(k, "")).strip()
            if v:
                out[k] = re.sub(r"\s+", " ", v)
        return out
    except Exception:
        return {}


# ── Deep Markdown generation ───────────────────────────────────────────────

def _generate_deep_md(
    card: dict[str, Any],
    report: dict[str, Any],
    similar: list[dict[str, Any]],
    api_provider: str,
    api_key: str,
    model: str,
    downloaded_pdf_url: str = "",
    source_pdf_url: str = "",
    local_pdf_path: str = "",
) -> str:
    """Generate a rich long-form Markdown literature review for one paper."""
    title = card.get("title", "Untitled")
    venue = card.get("venue", "")
    date = card.get("date", "")
    abstract = card.get("source_abstract", "")
    link = card.get("link", "") or _best_link(card)
    paper_id = card.get("paper_id", "")
    scores = card.get("scores") or {}
    full_text_cap = 16000 if _normalize_api_provider(api_provider) == "gemini" else 40000
    full_text = (card.get("source_content", "") or "")[:full_text_cap]

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

    def _prompt_safe_figure_url(url: str) -> str:
        s = (url or "").strip()
        if s.startswith("data:image/"):
            # Do not inject huge base64 payloads into LLM prompts.
            return f"[embedded-data-image omitted; length={len(s)}]"
        return s

    def _repair_image_links(markdown_text: str, figs: list[dict[str, str]]) -> str:
        """Replace placeholder/invalid image URLs (e.g., '(url)') with real figure URLs."""
        if not markdown_text or "![" not in markdown_text or not figs:
            return markdown_text

        figure_urls = [str(f.get("url", "")).strip() for f in figs if str(f.get("url", "")).strip()]
        if not figure_urls:
            return markdown_text

        def _is_valid_image_link(u: str) -> bool:
            s = (u or "").strip()
            if not s:
                return False
            if s.startswith("http://") or s.startswith("https://") or s.startswith("/api/") or s.startswith("data:image/"):
                return True
            return False

        img_pat = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")

        def _pick_url(alt_text: str, cur_url: str) -> str:
            cur = (cur_url or "").strip()
            if _is_valid_image_link(cur):
                return cur
            if cur.lower() in {"url", "<url>", "figure_url", "image_url", "link", "#"}:
                pass
            # If alt mentions "Figure N", try that index; else fallback to top-ranked figure.
            m = re.search(r"(?:figure|fig)\s*(\d+)", alt_text or "", re.IGNORECASE)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(figure_urls):
                    return figure_urls[idx]
            return figure_urls[0]

        def _repl(match: re.Match[str]) -> str:
            alt = match.group(1) or ""
            old_url = match.group(2) or ""
            new_url = _pick_url(alt, old_url)
            return f"![{alt}]({new_url})"

        return img_pat.sub(_repl, markdown_text)

    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _rank_figures_for_method(figs: list[dict[str, str]], context_text: str) -> list[dict[str, str]]:
        if not figs:
            return []
        ctx = (context_text or "").lower()
        ctx_tokens = set(re.findall(r"[a-z][a-z0-9_-]{3,}", ctx))
        stop = {
            "this", "that", "with", "from", "into", "over", "under", "between", "using",
            "paper", "study", "result", "results", "data", "model", "method", "methods",
            "analysis", "approach", "based", "their", "these", "those", "which", "were",
            "been", "have", "has", "into", "after", "before", "across", "section",
        }
        ctx_tokens = {t for t in ctx_tokens if t not in stop}

        # Strong method-diagram indicators
        method_terms_strong = {
            "overview", "proposed", "schematic", "illustration", "diagram",
            "pipeline", "framework", "architecture", "workflow",
        }
        # Moderate method indicators
        method_terms_mod = {
            "algorithm", "objective", "loss", "training", "inference", "module",
            "backbone", "encoder", "decoder", "attention", "transformer",
            "graph", "network", "component", "structure", "design",
        }
        # Result figures — still useful, give them a moderate boost
        result_terms = {
            "performance", "accuracy", "comparison", "benchmark",
            "auroc", "auc", "f1", "precision", "recall", "roc curve",
        }
        # Truly irrelevant content to penalise
        non_method_terms = {
            "supplementary", "appendix",
        }

        def _score_breakdown(fig: dict[str, str]) -> tuple[int, int, int]:
            """Returns (method_score, result_score, total_score)."""
            cap = str(fig.get("caption", "") or "").lower()
            url = str(fig.get("url", "") or "").lower()
            src = str(fig.get("source", "") or "").lower()
            text = f"{cap} {url}"
            m_score = 0
            r_score = 0

            for t in method_terms_strong:
                if t in text:
                    m_score += 6
            for t in method_terms_mod:
                if t in text:
                    m_score += 3
            for t in result_terms:
                if t in text:
                    r_score += 4
            for t in non_method_terms:
                if t in text:
                    m_score -= 3
                    r_score -= 3

            if re.search(r"\b(our|we propose|proposed method|proposed framework)\b", cap):
                m_score += 5

            fig_tokens = set(re.findall(r"[a-z][a-z0-9_-]{3,}", text))
            overlap = len((fig_tokens & ctx_tokens) - stop)
            m_score += min(overlap * 2, 12)

            fig_num_m = re.search(r"(?:figure|fig)[.\s]*(\d+)", cap)
            if fig_num_m:
                n = int(fig_num_m.group(1))
                if 2 <= n <= 5:
                    m_score += 2
                elif n == 1:
                    m_score -= 2
                    r_score -= 1
                elif n >= 8:
                    m_score -= 1

            if src in {"arxiv", "html"}:
                m_score += 2
                r_score += 2
            elif src.startswith("pdf"):
                m_score -= 1
                r_score -= 1
            if re.search(r"^pdf figure\s*\d+", cap):
                m_score -= 4
                r_score -= 4

            total = max(m_score, r_score)
            return m_score, r_score, total

        def _classify(fig: dict[str, str]) -> str:
            m, r, _ = _score_breakdown(fig)
            if m <= 0 and r <= 0:
                return "unknown"
            if r > m:
                return "result"
            return "method"

        def _total_score(fig: dict[str, str]) -> int:
            return _score_breakdown(fig)[2]

        for fig in figs:
            fig["fig_type"] = _classify(fig)

        ranked = sorted(figs, key=_total_score, reverse=True)
        return ranked

    def _ai_reorder_figures_for_method(figs: list[dict[str, str]]) -> list[dict[str, str]]:
        """Use AI to pick and classify figures as method or result (best-effort)."""
        if not figs or len(figs) < 2 or not api_key.strip():
            return figs
        try:
            client = _make_openai_compatible_client(api_provider, api_key)
            candidates = []
            for i, f in enumerate(figs[:10], start=1):
                candidates.append({
                    "index": i,
                    "caption": str(f.get("caption", "") or ""),
                    "source": str(f.get("source", "") or ""),
                    "heuristic_type": str(f.get("fig_type", "unknown")),
                    "url_hint": str(f.get("url", "") or "")[:120],
                })
            methods_text = str(report.get("methods_detailed", "") or "")[:600]
            sys_prompt = (
                "You are selecting and classifying figures for a paper review.\n"
                "For each selected figure, assign a type:\n"
                "  'method': architecture/pipeline/framework/module diagrams — shows HOW the method works\n"
                "  'result': quantitative tables, plots, or qualitative comparisons — shows HOW WELL it works\n"
                "SKIP: motivation figures (usually Fig 1), related-work, dataset overview, supplementary.\n"
                "Select at most 1 method figure and 1 result figure (2 total). "
                "Return ONLY JSON: {\"picks\": [{\"index\": N, \"type\": \"method\"|\"result\"}, ...]}"
            )
            payload = {
                "title": title,
                "abstract": abstract[:400],
                "methods_summary": methods_text,
                "candidates": candidates,
            }
            raw = _create_text_completion(
                client=client,
                provider=api_provider,
                model=model.strip() or _default_model_for_provider(_normalize_api_provider(api_provider)),
                system_prompt=sys_prompt,
                user_content=json.dumps(payload, ensure_ascii=False),
                max_output_tokens=160,
                timeout=12,
            )
            s = raw.strip()
            if "{" in s and "}" in s:
                s = s[s.find("{"): s.rfind("}") + 1]
            obj = json.loads(s)
            picks_raw = obj.get("picks", [])
            if not isinstance(picks_raw, list):
                return figs
            seen_method = seen_result = False
            head: list[dict[str, str]] = []
            used: set[int] = set()
            for item in picks_raw:
                try:
                    iv = int(item.get("index", 0))
                    ftype = str(item.get("type", "")).strip().lower()
                except Exception:
                    continue
                if not (1 <= iv <= len(figs)):
                    continue
                if ftype == "method" and not seen_method:
                    figs[iv - 1]["fig_type"] = "method"
                    head.append(figs[iv - 1])
                    used.add(iv)
                    seen_method = True
                elif ftype == "result" and not seen_result:
                    figs[iv - 1]["fig_type"] = "result"
                    head.append(figs[iv - 1])
                    used.add(iv)
                    seen_result = True
            if not head:
                return figs
            tail = [f for j, f in enumerate(figs, start=1) if j not in used]
            return head + tail
        except Exception:
            return figs

    def _is_generic_pdf_caption(fig: dict[str, str]) -> bool:
        cap = str(fig.get("caption", "") or "").strip().lower()
        src = str(fig.get("source", "") or "").strip().lower()
        if not src.startswith("pdf"):
            return False
        return bool(re.match(r"^pdf figure\s*\d+(\s*\(page\s*\d+\))?$", cap))

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

    # Disable auto figure extraction/insertion for report stability.
    # Users can add figures manually in the report editor.
    figures_data: list[dict[str, str]] = []

    if api_key.strip():
        try:
            client = _make_openai_compatible_client(api_provider, api_key)

            # Build figure reference block for the prompt, grouped by pre-classified type
            if figures_data:
                method_figs = [(i + 1, f) for i, f in enumerate(figures_data[:6])
                               if f.get("fig_type") == "method"][:1]
                result_figs = [(i + 1, f) for i, f in enumerate(figures_data[:6])
                               if f.get("fig_type") == "result"][:1]
                fig_blocks: list[str] = []
                if method_figs:
                    lines = []
                    for i, f in method_figs:
                        cap = f.get("caption") or f"Figure {i}"
                        lines.append(f"Figure {i} [METHOD]: {cap}\nURL: {_prompt_safe_figure_url(str(f.get('url', '')))}")
                    fig_blocks.append("METHOD FIGURE (embed inside ## Method Details):\n" + "\n\n".join(lines))
                if result_figs:
                    lines = []
                    for i, f in result_figs:
                        cap = f.get("caption") or f"Figure {i}"
                        lines.append(f"Figure {i} [RESULT]: {cap}\nURL: {_prompt_safe_figure_url(str(f.get('url', '')))}")
                    fig_blocks.append("RESULT FIGURE (embed inside ## Main Results):\n" + "\n\n".join(lines))

                if fig_blocks:
                    fig_instructions = (
                        "\n\nPRE-CLASSIFIED FIGURES — embed each in its designated section:\n\n"
                        + "\n\n".join(fig_blocks)
                        + "\n\nFIGURE EMBEDDING RULES:\n"
                        "- Embed each figure in the section shown above — METHOD figure in '## Method Details', "
                        "RESULT figure in '## Main Results'.\n"
                        "- Place immediately after the paragraph it best illustrates.\n"
                        "- Syntax: ![Figure N: caption](url)\n"
                        "- Do NOT create a standalone figures section.\n"
                    )
                else:
                    fig_instructions = ""
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
                "Datasets, split, key metrics, and strongest baseline comparisons.\n\n"
                "## Main Results\n"
                "Key quantitative findings (about 100-160 words). Cover: best numbers on main benchmarks, "
                "most important comparisons vs baselines, and one or two notable ablation findings if available. "
                "Be specific — include actual numbers (e.g. '+2.3% AUROC over prior SOTA'). "
                "Embed a result figure here if one is available and clearly informative.\n\n"
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

            provider_norm = _normalize_api_provider(api_provider)
            primary_model = model.strip() or _default_model_for_provider(provider_norm)
            if provider_norm == "gemini":
                model_chain = [primary_model, "gemini-2.5-flash-lite"]
            else:
                model_chain = [primary_model, "gpt-4.1-mini"]
            # Deduplicate while preserving order
            model_chain = list(dict.fromkeys(model_chain))

            for m in model_chain:
                deep_max_tokens = 4000 if _normalize_api_provider(api_provider) == "gemini" else 8192
                cand_raw = _create_text_completion(
                    client=client,
                    provider=api_provider,
                    model=m,
                    system_prompt=sys_prompt,
                    user_content=user_content,
                    max_output_tokens=deep_max_tokens,
                    timeout=25,
                )
                cand = _normalize_math_delimiters(cand_raw)
                ok, reason = _is_deep_enough(cand)
                if ok:
                    md_body = cand
                    ai_error = ""
                    break
                ai_error = f"incomplete AI output ({m}): {reason}"
        except Exception as exc:
            ai_error = str(exc)
    else:
        ai_error = "Missing API key for selected provider"

    # Parse TAGS line from first line of AI output
    tags: list[str] = []
    if md_body:
        first_line = md_body.splitlines()[0]
        if first_line.startswith("TAGS:"):
            raw_tags = first_line[5:].strip()
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            md_body = md_body[len(first_line):].lstrip("\n")
    md_body = _normalize_math_delimiters(md_body)
    md_body = _repair_image_links(md_body, figures_data)
    # If AI output omitted images but we found classified figures, inject them by type.
    if figures_data and "![" not in md_body:
        method_fig = next((f for f in figures_data if f.get("fig_type") == "method"), None)
        result_fig = next((f for f in figures_data if f.get("fig_type") == "result"), None)
        for fig, section_pat in [
            (method_fig, r"##\s+Method Details"),
            (result_fig, r"##\s+Main Results"),
        ]:
            if not fig:
                continue
            cap = fig.get("caption") or ""
            label = "Figure"
            block = f"![{label}: {cap}]({fig['url']})"
            md_body = re.sub(
                rf"(?ms)({section_pat}[^\n]*\n)",
                r"\1\n" + block + "\n\n",
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
            for i, f in enumerate(figures_data[:1], start=1):
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

    # Scorecard (clean table + concise reasons)
    score_rows = []
    reason_lines = []
    reasons = scores.get("reasons") if isinstance(scores, dict) else None
    reasons = reasons if isinstance(reasons, dict) else {}
    merged_reasons: dict[str, str] = {}
    for k in ("relevance", "novelty", "rigor", "impact"):
        rv = str(reasons.get(k, "")).strip()
        if rv:
            merged_reasons[k] = rv

    need_ai_reasons = any(_score_reason_is_generic(merged_reasons.get(k, "")) for k in ("relevance", "novelty", "rigor", "impact"))
    if need_ai_reasons:
        ai_reason_map = _generate_ai_score_reasons(
            api_provider=api_provider,
            api_key=api_key,
            model=model,
            title=title,
            venue=venue,
            date=date,
            abstract=abstract,
            report=report,
            full_text=full_text,
            scores=scores if isinstance(scores, dict) else {},
        )
        for k, v in ai_reason_map.items():
            if v:
                merged_reasons[k] = v

    for k in ("relevance", "novelty", "rigor", "impact"):
        v = scores.get(k) if isinstance(scores, dict) else None
        if isinstance(v, (int, float)):
            iv = int(round(float(v)))
            bar = "█" * int(iv / 10) + "░" * (10 - int(iv / 10))
            reason = str(merged_reasons.get(k, "")).strip() or "-"
            score_rows.append(f"| {k.capitalize()} | {iv}/100 | {bar} |")
            if reason != "-":
                reason_lines.append(f"- **{k.capitalize()}**: {reason}")
    score_block = ""
    if score_rows:
        score_block = (
            "\n## Scorecard\n\n"
            "| Dimension | Value | Bar |\n"
            "|-----------|-------|-----|\n"
            + "\n".join(score_rows)
            + "\n"
        )
        if reason_lines:
            score_block += "\n**Why these scores**\n\n" + "\n".join(reason_lines) + "\n"

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
            # Keep deep report language clean: this report is generated in English.
            # Historical archive summaries may be Chinese, so suppress those lines.
            if _contains_cjk(sim_summary):
                sim_summary = ""
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
        + (f" · [Downloaded PDF]({downloaded_pdf_url})" if downloaded_pdf_url else "")
        + (f" · [Source PDF]({source_pdf_url})" if (source_pdf_url and not downloaded_pdf_url) else "")
        + "\n"
        + score_block
        + "\n\n"
        + md_body
        + related_section
    )

    return md


def _localize_external_images(
    md: str,
    date_str: str,
    day_dir: Path,
    slug: str,
    log_cb: Any | None = None,
) -> str:
    """Download external HTTPS image URLs embedded in markdown to local assets."""
    import urllib.request as _ur

    ext_img_pat = re.compile(
        r'(!\[[^\]]*\]\()(https?://[^\s)]+\.(?:png|jpg|jpeg|webp|gif)(?:\?[^)]*)?)\)',
        re.IGNORECASE,
    )
    if not ext_img_pat.search(md):
        return md

    assets_dir = day_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    seen: dict[str, str] = {}
    fig_idx = [0]

    def _repl(m: re.Match) -> str:
        prefix = m.group(1)
        url = m.group(2)
        if url in seen:
            return f"{prefix}{seen[url]})"
        try:
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
            with _ur.urlopen(req, timeout=10) as resp:
                data = resp.read()
            if not data:
                return m.group(0)
            ext = Path(url.split("?")[0]).suffix.lower()
            if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                ext = ".png"
            fig_idx[0] += 1
            fname = f"{slug}_ext{fig_idx[0]}_{hashlib.sha256(data).hexdigest()[:10]}{ext}"
            (assets_dir / fname).write_bytes(data)
            local_url = f"/api/reports/{date_str}/assets/{fname}"
            seen[url] = local_url
            if callable(log_cb):
                log_cb(f"🖼️ Cached external figure: {fname}")
            return f"{prefix}{local_url})"
        except Exception:
            return m.group(0)

    return ext_img_pat.sub(_repl, md)


def _externalize_data_uri_images(
    md: str,
    date_str: str,
    day_dir: Path,
    slug: str,
    log_cb: Any | None = None,
) -> str:
    """Replace markdown data:image URIs with saved files under reports/{date}/assets."""
    if "data:image/" not in md:
        return md

    assets_dir = day_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    seen_hash_to_name: dict[str, str] = {}
    fig_idx = 0

    def _mime_ext(mime: str) -> str:
        m = mime.lower()
        if m.endswith("jpeg") or m.endswith("jpg"):
            return "jpg"
        if m.endswith("webp"):
            return "webp"
        if m.endswith("gif"):
            return "gif"
        return "png"

    pattern = re.compile(
        r"!\[([^\]]*)\]\((data:image/([a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\n\r]+))\)",
        flags=re.IGNORECASE,
    )

    def _repl(match: re.Match[str]) -> str:
        nonlocal fig_idx
        alt = match.group(1) or ""
        mime_sub = match.group(3) or "png"
        b64_data = (match.group(4) or "").replace("\n", "").replace("\r", "")
        if not b64_data:
            return match.group(0)
        try:
            raw = base64.b64decode(b64_data, validate=False)
        except Exception:
            return match.group(0)
        if not raw:
            return match.group(0)

        digest = hashlib.sha256(raw).hexdigest()[:16]
        fname = seen_hash_to_name.get(digest)
        if not fname:
            fig_idx += 1
            ext = _mime_ext(mime_sub)
            fname = f"{slug}_fig{fig_idx}_{digest}.{ext}"
            out_path = assets_dir / fname
            try:
                out_path.write_bytes(raw)
            except Exception:
                return match.group(0)
            seen_hash_to_name[digest] = fname
            if callable(log_cb):
                log_cb(f"🖼️ Saved embedded figure: {fname}")

        url = f"/api/reports/{date_str}/assets/{fname}"
        return f"![{alt}]({url})"

    return pattern.sub(_repl, md)


def _write_digest_md(
    date_str: str,
    report_cards: list[dict[str, Any]],
    also_notable: list[dict[str, Any]],
    day_dir: Path,
) -> None:
    """Write/overwrite digest.md for a given date from current report_cards + also_notable."""
    total = len(report_cards) + len(also_notable)
    lines: list[str] = [
        f"# 📚 Research Digest | {date_str}\n\n",
        f"**{total}** papers fetched · **{len(report_cards)}** deep reads · **{len(also_notable)}** also notable\n\n",
        "---\n\n",
        "## Deep Reads\n\n",
    ]
    for rc in report_cards:
        title = rc.get("title", "")
        venue = rc.get("venue", "")
        date = rc.get("date", "")
        link = rc.get("link", "") or _best_link(rc)
        summary = (rc.get("report") or {}).get("ai_feed_summary", "") or rc.get("ai_feed_summary", "")
        slug = _safe_slug(title)
        fname = f"{slug}.md"
        lines.append(f"### [{title}]({link})\n\n")
        lines.append(f"> **{venue}** | {date}\n\n")
        if summary:
            lines.append(f"{summary}\n\n")
        lines.append(f"[📖 Full Report](./{fname})\n\n---\n\n")

    if also_notable:
        lines.append("## Also Notable\n\n")
        by_venue: dict[str, list[dict]] = {}
        for c in also_notable:
            v = c.get("venue", "Other")
            by_venue.setdefault(v, []).append(c)
        for venue_name, cards in sorted(by_venue.items()):
            lines.append(f"### {venue_name}\n\n")
            for c in cards:
                t = c.get("title", "")
                lnk = c.get("link", "") or _best_link(c)
                summary_short = (c.get("ai_feed_summary") or c.get("value_assessment") or "")[:120]
                bullet = f"- [{t}]({lnk})" if lnk else f"- {t}"
                if summary_short:
                    bullet += f" — {summary_short}"
                lines.append(bullet + "\n")
            lines.append("\n")

    (day_dir / "digest.md").write_text("".join(lines), encoding="utf-8")


def _save_reports(
    date_str: str,
    report_cards: list[dict[str, Any]],
    also_notable: list[dict[str, Any]],
    api_provider: str,
    api_key: str,
    model: str,
    download_pdf: bool = True,
    log_cb: Any | None = None,
) -> Path:
    """Write per-paper .md files and a digest.md to reports/{date}/."""
    day_dir = REPORTS_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    paper_files: list[tuple[dict, Path]] = []
    total = len(report_cards)
    for idx, rc in enumerate(report_cards, start=1):
        if callable(log_cb):
            log_cb(f"📝 Saving deep report {idx}/{total}: {str(rc.get('title', ''))[:56]}…")
        slug = _safe_slug(rc.get("title", "paper"))
        downloaded_pdf_url = ""
        source_pdf_url = ""
        if download_pdf:
            downloaded_pdf_url, source_pdf_url = _maybe_download_pdf_for_report(rc, day_dir, slug, log_cb=log_cb)
        local_pdf_file = _local_pdf_file_for_report(day_dir, slug)
        if callable(log_cb) and downloaded_pdf_url:
            log_cb(f"📄 PDF downloaded for report {idx}/{total}")
        # Write back in-place so caller can re-save run with PDF URLs
        if downloaded_pdf_url:
            rc["downloaded_pdf_url"] = downloaded_pdf_url
        md = _generate_deep_md(
            rc,
            rc.get("report") or {},
            rc.get("similar") or [],
            api_provider,
            api_key,
            model,
            downloaded_pdf_url=downloaded_pdf_url,
            source_pdf_url=source_pdf_url,
            local_pdf_path=str(local_pdf_file) if local_pdf_file.exists() else "",
        )
        md = _externalize_data_uri_images(md, date_str=date_str, day_dir=day_dir, slug=slug, log_cb=log_cb)
        md = _localize_external_images(md, date_str=date_str, day_dir=day_dir, slug=slug, log_cb=log_cb)
        fpath = day_dir / f"{slug}.md"
        fpath.write_text(md, encoding="utf-8")
        paper_files.append((rc, fpath))

    _write_digest_md(date_str, report_cards, also_notable, day_dir)

    return day_dir


# ── Pydantic models ────────────────────────────────────────────────────────

class Settings(BaseModel):
    language: str = "en"
    timezone: str = "UTC"
    journals: list[str] = []
    custom_journals: list[str] = []
    fields: list[str] = []
    download_pdf: bool = True
    api_provider: str = "gemini"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    api_model: str = "gemini-2.5-flash-lite"
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


class CachePdfRequest(BaseModel):
    date: str
    card: dict[str, Any]


# ── Note generation ────────────────────────────────────────────────────────

def _generate_note(
    card: dict[str, Any],
    report: dict[str, Any],
    similar: list[dict[str, Any]],
    api_provider: str,
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
            client = _make_openai_compatible_client(api_provider, api_key)

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

            note_body = _create_text_completion(
                client=client,
                provider=api_provider,
                model=model or _default_model_for_provider(api_provider),
                system_prompt=sys_prompt,
                user_content=json.dumps({
                    "title": title, "venue": venue, "date": date,
                    "abstract": abstract,
                    "methods": report.get("methods_detailed", ""),
                    "conclusion": report.get("main_conclusion", ""),
                    "full_text": (card.get("source_content", "") or "")[:15000],
                }, ensure_ascii=False),
                max_output_tokens=1200,
            )
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

    reading_space = (
        "\n---\n\n"
        "## 📝 Reading Notes\n\n"
        "> Write your thoughts, questions, or follow-up ideas here…\n\n"
        "&nbsp;\n"
    )

    return (
        f"# {title}\n\n"
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
    provider = _normalize_api_provider(cfg.get("api_provider", os.getenv("DEFAULT_API_PROVIDER", "gemini")))
    timezone = _normalize_timezone(cfg.get("timezone", os.getenv("APP_TIMEZONE", "UTC")))
    return {
        "app_mode": _app_mode(),
        "beta_daily_note_limit": _beta_note_daily_limit(),
        "beta_forced_model": _beta_forced_model() if _is_beta_mode() else "",
        "language": cfg.get("language", "en"),
        "timezone": timezone,
        "journals": cfg.get("journals", []),
        "custom_journals": cfg.get("custom_journals", []),
        "fields": cfg.get("fields", []),
        "download_pdf": bool(cfg.get("download_pdf", True)),
        "api_provider": provider,
        # Never expose env-level default API key to clients.
        "openai_api_key": cfg.get("openai_api_key", ""),
        "gemini_api_key": cfg.get("gemini_api_key", ""),
        "api_model": _effective_model(cfg.get("api_model", _default_model_for_provider(provider)), provider),
        "max_reports": _clamp_max_reports(cfg.get("max_reports", 5)),
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
    data = body.model_dump()
    provider = _normalize_api_provider(data.get("api_provider", "gemini"))
    data["api_provider"] = provider
    data["timezone"] = _normalize_timezone(data.get("timezone", "UTC"))
    data["api_model"] = _effective_model(data.get("api_model"), provider)
    data["max_reports"] = _clamp_max_reports(data.get("max_reports", 5))
    cfg.update(data)
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
    def _parse_meta(path: Path) -> tuple[str, list[str]]:
        title = path.stem
        tags: list[str] = []
        try:
            text = path.read_text("utf-8", errors="ignore")
            m = re.match(r"(?s)^---\n(.*?)\n---\n", text)
            if m:
                fm = m.group(1)
                t = re.search(r'^title:\s*"?([^"\n]+)"?\s*$', fm, re.MULTILINE)
                if t:
                    title = t.group(1).strip()
                tagm = re.search(r"^tags:\s*\[([^\]]*)\]\s*$", fm, re.MULTILINE)
                if tagm:
                    tags = [
                        x.strip().strip('"').strip("'")
                        for x in tagm.group(1).split(",")
                        if x.strip()
                    ]
        except Exception:
            pass
        return title, tags

    files = [
        (lambda title, tags: {"name": f.name, "size": f.stat().st_size, "title": title, "tags": tags})(*_parse_meta(f))
        for f in sorted(day_dir.glob("*.md"))
    ]
    return {"date": date, "path": str(day_dir), "files": files}


@app.get("/api/reports/{date}/{filename}")
def get_report_file(date: str, filename: str) -> dict[str, Any]:
    # "assets" is a sub-collection, not a file — delegate to list endpoint
    if filename == "assets":
        return list_report_assets(date)  # type: ignore[return-value]
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


@app.get("/api/reports/{date}/assets/{filename}")
def get_report_asset(date: str, filename: str):
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    ext = Path(filename).suffix.lower()
    media_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_map.get(ext)
    if not media_type:
        raise HTTPException(status_code=400, detail="Unsupported asset type")
    fpath = REPORTS_DIR / date / "assets" / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(
        path=str(fpath),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/api/reports/{date}/assets")
def list_report_assets(date: str) -> dict[str, Any]:
    assets_dir = REPORTS_DIR / date / "assets"
    if not assets_dir.exists():
        return {"date": date, "files": []}
    files = [
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(assets_dir.glob("*.pdf"))
    ]
    return {"date": date, "files": files}


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
    tz_name = _normalize_timezone(body.settings.timezone)
    _check_beta_note_limit_or_raise(tz_name)
    api_provider, api_key = _resolve_provider_and_key(
        body.settings.api_provider,
        body.settings.openai_api_key,
        body.settings.gemini_api_key,
    )
    model = _effective_model(body.settings.api_model, api_provider)
    card = dict(body.card)
    card["link"] = _best_link(card)

    note_md = _generate_note(card, body.report, body.similar, api_provider, api_key, model)

    # Save note into NOTES_DIR and pin it under "AI Paper Notes" folder metadata.
    base_slug = _safe_slug(card.get("title", "paper"), max_len=40)
    date_tag = re.sub(r"[^0-9]", "", body.date) or datetime.now(ZoneInfo(tz_name)).strftime("%Y%m%d")
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

    _mark_beta_note_usage(tz_name)

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
    api_provider, api_key = _resolve_provider_and_key(
        s.api_provider,
        s.openai_api_key,
        s.gemini_api_key,
    )
    model = _effective_model(s.api_model, api_provider)

    cfg = _load_config()
    archive_db = s.archive_db or cfg.get("archive_db", DEFAULT_ARCHIVE)

    settings_dict: dict[str, Any] = {
        "language": (s.language or "en"),
        "keywords": "",
        "api_provider": api_provider,
        "openai_api_key": api_key,
        "gemini_api_key": api_key if api_provider == "gemini" else "",
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
        slug = _safe_slug(card.get("title", "paper"))
        downloaded_pdf_url = ""
        source_pdf_url = ""
        if s.download_pdf:
            downloaded_pdf_url, source_pdf_url = _maybe_download_pdf_for_report(card, day_dir, slug)
        local_pdf_file = _local_pdf_file_for_report(day_dir, slug)
        md = _generate_deep_md(
            card,
            report,
            similar,
            api_provider,
            api_key,
            model,
            downloaded_pdf_url=downloaded_pdf_url,
            source_pdf_url=source_pdf_url,
            local_pdf_path=str(local_pdf_file) if local_pdf_file.exists() else "",
        )
        md = _externalize_data_uri_images(md, date_str=date_str, day_dir=day_dir, slug=slug)
        md = _localize_external_images(md, date_str=date_str, day_dir=day_dir, slug=slug)
        fpath = day_dir / f"{slug}.md"
        fpath.write_text(md, encoding="utf-8")
        md_path = str(fpath)
    except Exception:
        pass

    # Promote paper from also_notable to report_cards in the stored run,
    # then regenerate digest.md to reflect the updated counts.
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

                # Regenerate digest.md with updated lists
                try:
                    day_dir_digest = REPORTS_DIR / date_str
                    day_dir_digest.mkdir(parents=True, exist_ok=True)
                    _write_digest_md(date_str, report_cards, also_notable, day_dir_digest)
                except Exception:
                    pass
    except Exception:
        pass

    return {"ok": True, "report": report, "md_path": md_path, "card": card}


@app.post("/api/papers/cache-pdf")
def cache_pdf(body: CachePdfRequest) -> dict[str, Any]:
    """Attempt to download/cache a paper PDF under reports/{date}/assets/ and return local URL."""
    card = dict(body.card or {})
    title = card.get("title", "")
    if not title:
        raise HTTPException(status_code=400, detail="Missing card.title")
    day_dir = REPORTS_DIR / body.date
    day_dir.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(title)
    ok, local_url, detail = _try_download_pdf_for_report(card, day_dir, slug)
    return {
        "ok": ok,
        "downloaded_pdf_url": local_url,
        "source_pdf_url": detail if ok else "",
        "reason": "" if ok else detail,
        "slug": slug,
    }


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

    cfg = _load_config()
    tz_name = _normalize_timezone(body.settings.timezone or str(cfg.get("timezone", "")))

    # Check if today's run already exists.
    # In beta mode, force override is disabled to enforce daily quota.
    if _is_beta_mode() or (not body.force):
        today_str = _today_in_tz(tz_name)
        _archive_db = body.settings.archive_db or cfg.get("archive_db", DEFAULT_ARCHIVE)
        try:
            from paper_archive import get_run as _get_run
            if _get_run(_archive_db, today_str) is not None:
                reason = "beta_daily_limit" if _is_beta_mode() else "already_run_today"
                return {"started": False, "reason": reason, "date": today_str}
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
    api_provider, api_key = _resolve_provider_and_key(
        s.api_provider,
        s.openai_api_key or str(cfg.get("openai_api_key", "")),
        s.gemini_api_key or str(cfg.get("gemini_api_key", "")),
    )
    model = _effective_model(s.api_model, api_provider)

    settings_dict: dict[str, Any] = {
        "language": (s.language or "en"),
        "timezone": tz_name,
        "keywords": "",
        "exclude_keywords": s.exclude_keywords,
        "journals": s.journals + s.custom_journals,
        "strict_journal_only": s.strict_journal,
        "push_schedule": "daily",
        "custom_days": s.date_days,
        "date_range_days": s.date_days,
        "api_provider": api_provider,
        # Keep compatibility with legacy generator that reads only openai_api_key.
        "openai_api_key": api_key,
        "gemini_api_key": api_key if api_provider == "gemini" else "",
        "api_model": model,
        "enable_webhook_push": bool((s.webhook_url or "").strip()),
        "webhook_url": s.webhook_url,
    }
    archive_db = s.archive_db or cfg.get("archive_db", DEFAULT_ARCHIVE)
    max_reports = _clamp_max_reports(s.max_reports)

    def _run() -> None:
        try:
            from research_pipeline import (
                DEFAULT_SIMILAR_LIMIT,
                _build_slack_text,
                _deliver,
                _generate_report,
            )
            from paper_archive import find_similar, init_archive, store_paper, store_run, archive_size
            from app import build_digest, build_runtime_prefs_from_settings, fetch_candidates
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from datetime import datetime

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

            date_str = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
            slack_text = _build_slack_text(
                date_str=date_str,
                report_cards=report_cards,
                also_notable=also_for_push,
                total_fetched=len(all_cards),
                lang=str(settings_dict.get("language", "en")),
            )
            store_run(archive_db, date_str, report_cards, also_for_push, slack_text)

            # Deliver digest via webhook when configured.
            try:
                ok_push, push_msg = _deliver(
                    webhook_override="",
                    settings=settings_dict,
                    lang=str(settings_dict.get("language", "en")),
                    date_str=date_str,
                    text=slack_text,
                    digest=digest,
                )
                if ok_push:
                    _pipeline_log(f"📬 Webhook push sent: {push_msg}")
                else:
                    _pipeline_log(f"⚠️ Webhook push skipped/failed: {push_msg}")
            except Exception as push_exc:
                _pipeline_log(f"⚠️ Webhook push error: {push_exc}")

            _pipeline_log("📝 Saving markdown reports…")
            try:
                _save_reports(
                    date_str,
                    report_cards,
                    also_for_push,
                    api_provider,
                    api_key,
                    model,
                    download_pdf=bool(s.download_pdf),
                    log_cb=_pipeline_log,
                )
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
