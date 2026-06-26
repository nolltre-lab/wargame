#!/usr/bin/env bash
# start.sh — start wargame backend + frontend
# Kills any stale processes on :8000/:5173 first (clears in-memory sim state).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

# ── Colours ───────────────────────────────────────────────────────────────────
B='\033[0;34m'   # blue
Y='\033[0;33m'   # yellow
G='\033[0;32m'   # green
R='\033[0;31m'   # red
N='\033[0m'      # reset

# ── Cleanup on Ctrl+C / exit ──────────────────────────────────────────────────
cleanup() {
  echo ""
  echo -e "${R}Stopping wargame...${N}"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "Done."
}
trap cleanup EXIT INT TERM

# ── Kill stale port holders (clears previous sim state) ───────────────────────
echo -e "${Y}Clearing stale processes...${N}"
for port in 8000 5173; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo -e "  port ${port}: killing PID(s) ${pids}"
    echo "$pids" | xargs kill -9 2>/dev/null || true
  fi
done
sleep 0.4

# ── Dependency checks ─────────────────────────────────────────────────────────
if ! command -v uvicorn &>/dev/null; then
  echo -e "${Y}uvicorn not found — installing Python deps...${N}"
  pip3 install -r "$ROOT/sim/requirements.txt"
fi

if [ ! -d "$ROOT/ui/node_modules" ]; then
  echo -e "${Y}node_modules missing — running npm install...${N}"
  (cd "$ROOT/ui" && npm install)
fi

# ── Backend ───────────────────────────────────────────────────────────────────
echo -e "\n${B}[BACKEND]${N}  starting uvicorn on :8000"
(cd "$ROOT/sim" && uvicorn main:app --reload --port 8000) &
PIDS+=($!)

# Give uvicorn a moment to bind the port before vite starts
sleep 1

# ── Frontend ──────────────────────────────────────────────────────────────────
echo -e "${G}[FRONTEND]${N} starting Vite on :5173"
(cd "$ROOT/ui" && npm run dev) &
PIDS+=($!)

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${B}http://localhost:8000${N}  — backend / REST / WebSocket"
echo -e "  ${G}http://localhost:5173${N}  — frontend"
echo ""
echo -e "  Ctrl+C to stop both and clear sim state."
echo ""

wait
