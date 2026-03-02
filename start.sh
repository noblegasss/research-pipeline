#!/bin/bash
# Research Pipeline — one-click start
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "🚀 Starting Research Pipeline..."

# Start FastAPI backend (port 8010)
python3 -m uvicorn api.main:app --reload --port 8010 &
PID_API=$!

# Start Next.js frontend (port 3010)
npm --prefix web run dev -- -p 3010 &
PID_WEB=$!

echo ""
echo "✅ API:  http://localhost:8010"
echo "✅ Web:  http://localhost:3010"
echo ""
echo "Press Ctrl+C to stop both servers"

trap "kill $PID_API $PID_WEB 2>/dev/null; exit" INT TERM
wait
