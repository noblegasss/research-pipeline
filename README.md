# Research Pipeline

An AI-powered daily research reading assistant. Set your journals and fields of interest — it fetches new papers, scores them for relevance, and generates structured deep-read reports complete with figures, math rendering, and related paper discovery. Everything is browsable through a local web UI.

> Built for researchers who want to stay current without spending hours scanning abstracts.

---

## What It Does

Each pipeline run:

1. **Fetches** newly published papers from your selected journals (Nature, Science, bioRxiv, etc.) and arXiv fields
2. **Scores** each paper on relevance, novelty, rigor, and impact using AI
3. **Selects** the top papers as deep reads (configurable limit)
4. **Generates** structured Markdown reports with:
   - AI Summary, Method Details, Main Results, Pros/Cons
   - Figures extracted from arXiv HTML or PDFs, classified as method vs. result
   - LaTeX math rendering (KaTeX)
   - Related papers from your personal archive
5. **Saves** everything locally — reports, notes, and a growing SQLite archive

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

This project uses [`research_push`](https://github.com/noblegasss/research_push) for paper fetching. Clone it as a sibling directory:

```
parent/
├── research_pipeline/   ← this repo
└── research_push/       ← paper fetching engine
```

---

## Quick Start

### 1. Clone both repos

```bash
git clone https://github.com/noblegasss/research_push
git clone https://github.com/noblegasss/research_pipeline
```

### 2. Backend

```bash
cd research_pipeline
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8010 --reload
```

### 3. Frontend

```bash
cd web
npm install
echo "BACKEND_API_BASE=http://127.0.0.1:8010" > .env.local
npm run dev
```

### 4. Run

1. Open `http://localhost:3000`
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
| **Journals** | Nature, Science, NEJM, bioRxiv, Cell, and more |
| **Fields** | AI, Bioinformatics, Aging, Oncology, etc. |
| **Max Reports** | Number of deep-read reports per run |
| **Date Range** | How many days back to look for new papers |
| **Download PDF** | Auto-download PDFs and extract figures |
| **Slack Webhook** | Optional — post a daily digest to Slack |

---

## Project Structure

```
api/                   FastAPI backend + pipeline orchestration
  main.py              All API routes and report generation logic
web/                   Next.js frontend
  app/
    runs/              Daily pipeline run history
    reports/           Per-paper deep report pages
    network/           Interactive paper similarity graph
    notes/             Obsidian-compatible reading notes
    search/            Full-text archive search
    settings/          Configuration UI
paper_archive.py       SQLite archive utilities
research_pipeline.py   CLI pipeline runner
pipeline_config.json   Local config (gitignored)
```

---

## Secrets & Security

`pipeline_config.json` stores your API keys locally and is gitignored by default. **Do not commit it.** See `.env.example` for optional environment variable overrides.

---

## License

MIT
