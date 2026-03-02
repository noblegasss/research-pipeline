# Research Pipeline

An AI-powered daily research reading assistant. Set your journals and fields of interest — it fetches new papers, scores them for relevance, and generates structured deep-read reports complete with figures, math rendering, and related paper discovery. Everything is browsable through a local web UI.

> Built for researchers who want to stay current without spending hours scanning abstracts.

---

## What It Does

Each pipeline run:

1. **Fetches** newly published papers from your selected journals (Nature, Science, Cell, bioRxiv, arXiv, etc.) via Crossref, PubMed, RSS, and arXiv APIs
2. **Deduplicates** against your personal archive — never see the same paper twice
3. **Scores** each paper on relevance, novelty, rigor, and impact using AI
4. **Selects** the top papers as deep reads (configurable limit)
5. **Generates** structured reports with:
   - AI Summary (2–3 paragraphs with key equations in LaTeX)
   - Method Details, Main Results, Pros/Cons, Future Directions
   - Figures extracted from arXiv HTML or PDFs, classified as method vs. result
   - KaTeX math rendering
   - Related papers from your personal archive
6. **Saves** everything locally — reports, notes, and a growing SQLite archive
7. **Notifies** via Slack with a daily digest (optional)

---

## Features

- **70+ journals supported** — Nature family, Science family, Cell family, Lancet, JAMA, NEJM, PNAS, eLife, bioRxiv, medRxiv, arXiv, Alzheimer's journals, aging journals, and more
- **Smart journal matching** — abbreviations and aliases are handled automatically (e.g. "Nat Med" → Nature Medicine, "Proc Natl Acad Sci" → PNAS)
- **Auto-schedule** — set a daily time and the pipeline runs automatically
- **Paper network graph** — D3.js interactive visualization of paper similarity (Jaccard + Gaussian kernel)
- **Demote papers** — move a deep read back to "also notable" if it's not relevant
- **Tag-based search** — multi-select tags with OR logic, normalized and deduplicated
- **Reading notes** — Obsidian-compatible markdown notes per paper
- **PWA support** — installable as a native app on macOS / Chrome

---

## Screenshots

<!-- TODO: Add screenshots of the report view, network graph, and run log -->

---

## Tech Stack

| | |
|---|---|
| **Frontend** | Next.js 16 + TypeScript + Tailwind CSS + D3.js |
| **Backend** | FastAPI + uvicorn |
| **Database** | SQLite |
| **AI** | OpenAI or Google Gemini (configurable) |

---

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- An OpenAI or Gemini API key

This project uses [`research_push`](https://github.com/noblegasss/research-push) for paper fetching. Clone it as a sibling directory:

```
parent/
├── research_pipeline/   ← this repo
└── research_push/       ← paper fetching engine
```

---

## Quick Start

### 1. Clone both repos

```bash
git clone https://github.com/noblegasss/research-push.git research_push
git clone https://github.com/noblegasss/research-pipeline.git research_pipeline
```

### 2. Install dependencies

```bash
cd research_pipeline
pip install -r requirements.txt
cd web && npm install && cd ..
```

### 3. Start services

**Option A: PM2 (recommended — auto-restart, background)**

```bash
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 save
```

**Option B: One-command script**

```bash
./start.sh
```

**Option C: Manual**

```bash
# Terminal 1 — API
uvicorn api.main:app --host 127.0.0.1 --port 8010 --reload

# Terminal 2 — Frontend
cd web && npm run dev -- -p 3010
```

### 4. Use

1. Open `http://localhost:3010`
2. Go to **Settings**
3. Enter your AI provider and API key (Gemini or OpenAI)
4. Select journals and research fields
5. Click **Run Pipeline**

---

## Configuration

All settings are managed through the web UI (**Settings** page) and saved locally to `pipeline_config.json` (gitignored — never committed).

| Setting | Description |
|---|---|
| **AI Provider** | `gemini` or `openai` |
| **API Key** | Your provider's API key |
| **Model** | e.g. `gemini-2.5-flash`, `gpt-4.1-mini` |
| **Journals** | Nature, Science, Cell, NEJM, bioRxiv, arXiv, and 70+ more |
| **Fields** | AI, Bioinformatics, Aging, Oncology, Genomics, etc. |
| **Max Reports** | Number of deep-read reports per run |
| **Date Range** | How many days back to look for new papers |
| **Download PDF** | Auto-download PDFs and extract figures |
| **Slack Webhook** | Optional — post deep-read digest to Slack |
| **Auto-schedule** | Daily auto-run at a configured time |

---

## Project Structure

```
api/                   FastAPI backend + pipeline orchestration
  main.py              All API routes, report generation, scheduler
web/                   Next.js frontend (PWA-enabled)
  app/
    runs/              Daily pipeline run history
    reports/           Per-paper deep report pages
    network/           Interactive paper similarity graph (D3.js)
    notes/             Obsidian-compatible reading notes
    search/            Full-text archive search with tag filtering
    settings/          Configuration UI
paper_archive.py       SQLite archive utilities
research_pipeline.py   CLI pipeline runner + Slack integration
ecosystem.config.cjs   PM2 process manager config
start.sh               One-command startup script
pipeline_config.json   Local config (gitignored)
```

---

## Secrets & Security

`pipeline_config.json` stores your API keys locally and is gitignored by default. **Do not commit it.**

---

## License

MIT
