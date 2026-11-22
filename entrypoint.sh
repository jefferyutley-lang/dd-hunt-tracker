#!/bin/sh
set -eu

PORT="${PORT:-10000}"
export PORT

# Substitute PORT into nginx config
envsubst '${PORT}' < /app/nginx.conf.template > /tmp/nginx.conf

# Tiny HTML login server on loopback (native form POST for iOS Keychain)
python /app/login_server.py &
LOGIN_PID=$!

# Streamlit on loopback only; nginx faces $PORT
streamlit run /app/app.py \
  --server.port=8501 \
  --server.address=127.0.0.1 \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.disconnectedSessionTTL=43200 &
STREAMLIT_PID=$!

# Wait briefly for both backends to bind
i=0
login_ready=0
streamlit_ready=0
while [ "$i" -lt 60 ]; do
  if [ "$login_ready" -eq 0 ] && wget -q -O /dev/null http://127.0.0.1:8502/health 2>/dev/null; then
    login_ready=1
  fi
  if [ "$streamlit_ready" -eq 0 ] && { wget -q -O /dev/null http://127.0.0.1:8501/_stcore/health 2>/dev/null \
     || wget -q -O /dev/null http://127.0.0.1:8501/ 2>/dev/null; }; then
    streamlit_ready=1
  fi
  if [ "$login_ready" -eq 1 ] && [ "$streamlit_ready" -eq 1 ]; then
    break
  fi
  if ! kill -0 "$LOGIN_PID" 2>/dev/null; then
    echo "Login server exited early" >&2
    exit 1
  fi
  if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
    echo "Streamlit exited early" >&2
    exit 1
  fi
  i=$((i + 1))
  sleep 0.5
done

if [ "$login_ready" -ne 1 ]; then
  echo "Login server did not become ready" >&2
  exit 1
fi

exec nginx -c /tmp/nginx.conf -g 'daemon off;'
