"""Paper archive for the research pipeline.

Maintains a SQLite database of all seen papers with keyword-based
Jaccard similarity search to surface related historical papers.
"""
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Words too common to be useful similarity signals
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "was", "are",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "that",
    "this", "these", "those", "it", "its", "we", "our", "they", "their",
    "which", "who", "how", "when", "where", "what", "why", "than",
    "then", "both", "also", "such", "more", "than", "not", "all",
    # Over-frequent academic stopwords
    "study", "using", "based", "paper", "method", "results", "show",
    "shown", "shows", "found", "find", "provide", "present", "however",
    "between", "among", "within", "across", "through", "significantly",
    "significant", "highly", "specific", "novel", "proposed", "model",
    "approach", "analysis", "result", "effect", "role", "impact",
    "data", "evaluation", "association", "research", "work", "article",
    "report", "review", "letter", "case", "note", "toward", "towards",
    "here", "there", "where", "large", "small", "high", "low", "new",
    "into", "about", "after", "before", "during", "under", "over",
    "each", "other", "most", "many", "some", "can", "than", "only",
    "used", "uses", "use", "via", "well", "thus", "while", "further",
    "recent", "current", "various", "several", "without", "between",
}


def _tokenize(text: str) -> set[str]:
    """Extract meaningful lowercase words (length >= 4) from text."""
    words = re.findall(r"[a-z]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def init_archive(db_path: str) -> None:
    """Initialize (or connect to) the SQLite paper archive."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                paper_id         TEXT PRIMARY KEY,
                title            TEXT NOT NULL,
                abstract         TEXT NOT NULL DEFAULT '',
                venue            TEXT NOT NULL DEFAULT '',
                publication_date TEXT NOT NULL DEFAULT '',
                report_json      TEXT NOT NULL DEFAULT '{}',
                stored_at        TEXT NOT NULL,
                word_bag         TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_date    TEXT PRIMARY KEY,
                papers_json TEXT NOT NULL DEFAULT '[]',
                slack_text  TEXT NOT NULL DEFAULT '',
                total_count INTEGER NOT NULL DEFAULT 0,
                stored_at   TEXT NOT NULL
            )
            """
        )
        conn.commit()


def store_run(
    db_path: str,
    run_date: str,
    report_cards: list[dict[str, Any]],
    also_notable: list[dict[str, Any]],
    slack_text: str,
) -> None:
    """Persist a pipeline run to the archive so it can be viewed later."""
    import copy

    def _clean(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip large binary-like fields that would bloat the JSON."""
        out = []
        for c in cards:
            cc = copy.copy(c)
            cc.pop("source_content", None)  # full-text PDF content, can be huge
            out.append(cc)
        return out

    payload = json.dumps(
        {
            "report_cards": _clean(report_cards),
            "also_notable": _clean(also_notable),
        },
        ensure_ascii=False,
    )
    total = len(report_cards) + len(also_notable)
    stored_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs
                (run_date, papers_json, slack_text, total_count, stored_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_date, payload, slack_text, total, stored_at),
        )
        conn.commit()


def list_runs(db_path: str) -> list[dict[str, Any]]:
    """Return all stored run dates, newest first."""
    if not Path(db_path).exists():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT run_date, total_count, stored_at FROM runs ORDER BY run_date DESC"
            ).fetchall()
        return [{"run_date": r[0], "total_count": r[1], "stored_at": r[2]} for r in rows]
    except Exception:
        return []


def get_run(db_path: str, run_date: str) -> dict[str, Any] | None:
    """Return full data for a specific run date."""
    if not Path(db_path).exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT run_date, papers_json, slack_text, total_count, stored_at "
                "FROM runs WHERE run_date = ?",
                (run_date,),
            ).fetchone()
        if not row:
            return None
        data = json.loads(row[1])
        return {
            "run_date": row[0],
            "report_cards": data.get("report_cards", []),
            "also_notable": data.get("also_notable", []),
            "slack_text": row[2],
            "total_count": row[3],
            "stored_at": row[4],
        }
    except Exception:
        return None


def store_paper(
    db_path: str,
    paper_id: str,
    title: str,
    abstract: str,
    venue: str,
    publication_date: str,
    report: dict[str, Any],
) -> None:
    """Upsert a paper (and its AI report) into the archive."""
    word_bag = " ".join(sorted(_tokenize(title + " " + abstract)))
    stored_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO papers
                (paper_id, title, abstract, venue, publication_date,
                 report_json, stored_at, word_bag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                title,
                abstract,
                venue,
                publication_date,
                json.dumps(report, ensure_ascii=False),
                stored_at,
                word_bag,
            ),
        )
        conn.commit()


def find_similar(
    db_path: str,
    title: str,
    abstract: str,
    exclude_paper_id: str = "",
    limit: int = 3,
    min_score: float = 0.08,
) -> list[dict[str, Any]]:
    """Return up to *limit* historically-seen papers most similar to the given title+abstract.

    Similarity is computed as Jaccard coefficient over the two papers'
    word bags (keyword tokens extracted from title + abstract).
    """
    if not Path(db_path).exists():
        return []
    query_words = _tokenize(title + " " + abstract)
    if not query_words:
        return []

    try:
        with sqlite3.connect(db_path) as conn:
            if exclude_paper_id:
                rows = conn.execute(
                    "SELECT paper_id, title, venue, publication_date, word_bag, report_json "
                    "FROM papers WHERE paper_id != ?",
                    (exclude_paper_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT paper_id, title, venue, publication_date, word_bag, report_json "
                    "FROM papers"
                ).fetchall()
    except Exception:
        return []

    scored: list[dict[str, Any]] = []
    for pid, ptitle, pvenue, pdate, wbag, report_json in rows:
        bag_words = set(wbag.split()) if wbag else set()
        score = _jaccard(query_words, bag_words)
        if score >= min_score:
            summary = ""
            try:
                parsed = json.loads(report_json or "{}")
                summary = (
                    parsed.get("ai_feed_summary")
                    or parsed.get("main_conclusion")
                    or parsed.get("methods_detailed")
                    or ""
                )
            except Exception:
                summary = ""
            if summary:
                summary = re.sub(r"\s+", " ", summary).strip()[:220]
            scored.append(
                {
                    "paper_id": pid,
                    "title": ptitle,
                    "venue": pvenue,
                    "date": pdate,
                    "score": score,
                    "summary": summary,
                }
            )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def archive_size(db_path: str) -> int:
    """Return the number of papers stored in the archive."""
    if not Path(db_path).exists():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
    except Exception:
        return 0


def get_known_paper_ids(db_path: str) -> set[str]:
    """Return the set of all paper_ids already in the archive."""
    if not Path(db_path).exists():
        return set()
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT paper_id FROM papers").fetchall()
            return {r[0] for r in rows if r[0]}
    except Exception:
        return set()
