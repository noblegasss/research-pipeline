# Research Pipeline

AI-powered research workflow for:
- fetching recent papers
- ranking and selecting deep reads
- generating structured deep reports
- creating linked notes
- browsing results in a Next.js UI

## Core Pipeline

The core workflow runs daily and does two key things:

1. Fetches newly published papers from your selected **journals** and **research fields**.
2. Uses AI to identify the most worth-reading papers, then generates concise summaries and deep-read reports.

## Project Structure

- `api/` FastAPI backend for pipeline + reports + notes APIs
- `web/` Next.js frontend
- `research_pipeline.py` CLI pipeline runner
- `pipeline_app.py` Streamlit UI runner
- `paper_archive.py` SQLite archive helpers

## Prerequisites

- Python `3.11+`
- Node.js `18+` and npm

## 1) Clone Repository

```bash
git clone <YOUR_REPO_URL> research_pipeline
```

## 2) Backend Setup

```bash
cd research_pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create env from example:

```bash
cp .env.example .env
```

Then start backend on port `8010`:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8010
```

## 3) Frontend Setup

```bash
cd web
npm install
```

Create frontend env:

```bash
cat > .env.local <<'EOF'
BACKEND_API_BASE=http://127.0.0.1:8010
EOF
```

Start frontend (default `3000`):

```bash
npm run dev
```

## 4) Open App

- Frontend: `http://localhost:3000`
- Go to Settings and set:
  - OpenAI key
  - language (`en` / `zh`)
  - journals / fields
- Click **Run Pipeline**

## Notes

- Runtime data (`reports/`, `notes/`, `paper_archive.db`) is git-ignored.
- Existing generated reports are static files; changing language affects newly generated content.
- If another app already uses your backend port, update:
  - backend run port
  - `BACKEND_API_BASE` in `web/.env.local`
