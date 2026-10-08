# Adaptive Learning OS — hosted server image (zero runtime deps).
# Serves the stdlib HTTP API + split-screen workspace UI. Auth is fail-closed:
# a public (non-loopback) bind REFUSES to start without ADAPTIVE_API_TOKEN.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8777
# ADAPTIVE_API_TOKEN is injected at runtime (compose env), never baked into the image.

WORKDIR /app

# Install the package (dependencies = [], so this stays small and offline-friendly).
COPY pyproject.toml MANIFEST.in README.md ./
COPY adaptive_learning_os ./adaptive_learning_os
COPY skills ./skills
RUN pip install --no-cache-dir . \
    && adduser --disabled-password --gecos "" appuser

# UI decks (served at /). Kept as a later layer so the install layer caches.
COPY decks ./decks

# Session data lives here (mount a volume to persist the append-only ledger).
RUN mkdir -p /app/.learning && chown -R appuser:appuser /app
USER appuser

EXPOSE 8777

# Liveness only (unauthenticated, no data).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8777')+'/health',timeout=3).status==200 else 1)"

# Bind 0.0.0.0 so a reverse proxy can reach it. No --allow-anon: without a token
# ServerConfig refuses to start (fail-closed), which is correct for a public bind.
CMD ["sh", "-c", "alearn --workspace /app serve --host 0.0.0.0 --port ${PORT} --ui /app/decks/workspace.html"]
