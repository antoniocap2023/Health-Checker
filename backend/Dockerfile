# syntax=docker/dockerfile:1
# ============================================================================
# Backend image: the FastAPI app served by uvicorn.
# An "image" is a frozen snapshot — OS + Python + our deps + our code + the
# command to start it. `docker build` bakes it once; `docker run` starts a
# container (a running instance) from it. Each instruction below is a cached
# LAYER; if a layer's inputs are unchanged, Docker reuses the cache and skips it.
# ============================================================================

# Start from an official Python image. "slim" is a smaller Debian variant (fewer
# preinstalled packages = smaller image, less to patch). The tag pins the version
# so the build is reproducible — not whatever "latest" happens to be today.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: don't litter the image with .pyc files.
# PYTHONUNBUFFERED: don't buffer stdout/stderr, so logs appear immediately in
# `docker logs` instead of being held back — important for seeing what the app does.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Every instruction below runs inside this directory in the image's filesystem.
WORKDIR /app

# ---- Dependency layer (the reason for the ordering) ------------------------
# Copy ONLY requirements.txt first, then install. Docker caches this layer keyed
# on the file's contents. Because requirements rarely change, rebuilds reuse the
# cached install and skip re-downloading packages. If we copied the source first,
# every code edit would invalidate this layer and reinstall everything — slow.
# Note: only RUNTIME deps (requirements.txt), never requirements-dev.txt, so the
# image doesn't ship pytest.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Application code ------------------------------------------------------
# Now copy the rest of the backend. Editing code invalidates only this cheap
# layer, not the expensive dependency layer above. (.dockerignore controls what
# is and isn't copied — notably it excludes .env so secrets never enter the image.)
COPY . .

# Run as a non-root user. If the app is ever compromised, the attacker isn't root
# inside the container. We create the user and hand it ownership of /app.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Document the port the app listens on (informational; actually publishing it
# happens at `docker run -p` / in docker-compose).
EXPOSE 8000

# The start command. uvicorn MUST bind 0.0.0.0 (all interfaces) — NOT the default
# 127.0.0.1 — or connections from outside the container can't reach it.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
