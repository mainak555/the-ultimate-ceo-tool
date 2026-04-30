FROM python:3.12-slim

# Standalone single-container image.
#
# Use this when you can only deploy ONE container (local dev, Vercel,
# HuggingFace Spaces, fly.io single-app, etc.). MCP servers run STDIO
# in-process via Node/npx. For multi-container deployments prefer
# `deployments/compose/` (sidecar) or `deployments/k8s/` (Helm chart).

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_MAJOR=20

WORKDIR /app

# System deps + Node.js LTS (for `npx` MCP servers spawned in-process).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        ca-certificates \
        curl \
        gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y curl gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY agents/ agents/
COPY config/ config/
COPY server/ server/
COPY core/ core/
COPY agent_models.json .
COPY manage.py .

# Compile static (SCSS via django-compressor)
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
