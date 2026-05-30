#!/bin/bash
# Call Me Tomorrow — start tunnel + bot server together
# Usage:  bash start.sh
#
# What this does:
#   1. Opens a localtunnel to port 7860 (free, no account needed)
#   2. Writes the public URL to .env as PUBLIC_URL
#   3. Starts the Pipecat bot server

set -e
cd "$(dirname "$0")"
PORT=7860
ENV_FILE=".env"

# ── Activate venv ─────────────────────────────────────────────────────────────
source .venv/bin/activate

# ── Kill any leftover tunnel ───────────────────────────────────────────────────
pkill -f "lt --port $PORT" 2>/dev/null || true

# ── Start localtunnel ─────────────────────────────────────────────────────────
echo "Opening tunnel on port $PORT..."
lt --port $PORT > /tmp/lt-cmt.log 2>&1 &
LT_PID=$!

# Wait up to 12s for URL
PUBLIC_URL=""
for i in $(seq 1 12); do
    PUBLIC_URL=$(grep -o 'https://[a-z0-9-]*\.loca\.lt' /tmp/lt-cmt.log 2>/dev/null | head -1)
    [ -n "$PUBLIC_URL" ] && break
    printf "."
    sleep 1
done
echo ""

if [ -z "$PUBLIC_URL" ]; then
    echo "⚠  Tunnel didn't start. Check /tmp/lt-cmt.log"
    echo "   Outbound calls won't work without a public URL."
else
    echo "✓  Tunnel: $PUBLIC_URL"
    # Write / update PUBLIC_URL in .env
    if grep -q "^PUBLIC_URL=" "$ENV_FILE" 2>/dev/null; then
        sed -i '' "s|^PUBLIC_URL=.*|PUBLIC_URL=$PUBLIC_URL|" "$ENV_FILE"
    else
        echo "PUBLIC_URL=$PUBLIC_URL" >> "$ENV_FILE"
    fi
    echo "   Written to .env"
fi

echo ""
echo "Starting server on http://localhost:$PORT"
echo "─────────────────────────────────────────"

# ── Start server ──────────────────────────────────────────────────────────────
trap "kill $LT_PID 2>/dev/null; exit" INT TERM
python bot.py
