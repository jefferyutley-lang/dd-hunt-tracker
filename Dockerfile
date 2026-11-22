FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx gettext-base wget \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Reports: matplotlib (Agg) font cache built at image time so the first report is quick
ENV MPLBACKEND=Agg MPLCONFIGDIR=/app/.mplconfig
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot"

COPY app.py club_auth.py reports.py login_server.py login.html .
COPY .streamlit/config.toml /app/.streamlit/config.toml
COPY dd_logo.png .
COPY farm_maps ./farm_maps
COPY supabase_farm_plans.sql .

# Icons and manifest served by nginx at site root
COPY apple-touch-icon.png apple-touch-icon-precomposed.png apple-touch-icon-120x120.png \
     favicon.ico favicon.png icon-192.png icon-512.png manifest.webmanifest \
     /app/static-icons/
COPY nginx.conf.template /app/nginx.conf.template
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Env vars SUPABASE_URL / SUPABASE_KEY are provided by Render at runtime
ENV PORT=10000
EXPOSE 10000

CMD ["/app/entrypoint.sh"]
