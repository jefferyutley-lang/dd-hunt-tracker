FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx gettext-base wget \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY dd_logo.png .
COPY farm_maps ./farm_maps
COPY supabase_farm_plans.sql .

# Icons served by nginx at site root
COPY apple-touch-icon.png apple-touch-icon-precomposed.png apple-touch-icon-120x120.png favicon.ico /app/static-icons/
COPY nginx.conf.template /app/nginx.conf.template
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Env vars SUPABASE_URL / SUPABASE_KEY are provided by Render at runtime
ENV PORT=10000
EXPOSE 10000

CMD ["/app/entrypoint.sh"]
