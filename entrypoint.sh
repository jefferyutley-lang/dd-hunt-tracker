#!/bin/sh
set -eu

PORT="${PORT:-10000}"
export PORT

# Substitute PORT into nginx config
envsubst '${PORT}' < /app/nginx.conf.template > /tmp/nginx.conf

# Streamlit on loopback only; nginx faces $PORT
streamlit run /app/app.py \
  --server.port=8501 \
  --server.address=127.0.0.1 \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false &
STREAMLIT_PID=$!

# Wait briefly for Streamlit to bind
i=0
while [ "$i" -lt 60 ]; do
  if wget -q -O /dev/null http://127.0.0.1:8501/_stcore/health 2>/dev/null \
     || wget -q -O /dev/null http://127.0.0.1:8501/ 2>/dev/null; then
    break
  fi
  # also ok if process still starting
  if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
    echo "Streamlit exited early" >&2
    exit 1
  fi
  i=$((i + 1))
  sleep 0.5
done

exec nginx -c /tmp/nginx.conf -g 'daemon off;'
