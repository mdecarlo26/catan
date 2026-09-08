# Backend image: FastAPI app served by Uvicorn.
# Built from ./backend as the build context (see docker-compose.yml).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- Dependencies layer -----------------------------------------------
# Copy only the dependency manifest first so this (slow) layer is
# cached and only re-runs when pyproject.toml's dependency list
# actually changes, not on every application code edit.
#
# There's no lock file yet, so rather than hand-duplicating the
# dependency list here (and risking drift), read it straight out of
# pyproject.toml with stdlib tomllib and pip-install it. Installs both
# the runtime deps ([project].dependencies) and the dev extras
# ([project.optional-dependencies].dev, e.g. pytest) so
# `docker compose run backend pytest` works against this same image.
COPY pyproject.toml ./
RUN python -c "\
import subprocess, tomllib; \
data = tomllib.load(open('pyproject.toml', 'rb')); \
project = data['project']; \
deps = list(project.get('dependencies', [])); \
deps += project.get('optional-dependencies', {}).get('dev', []); \
subprocess.check_call(['pip', 'install', '--no-cache-dir', *deps])"

# --- Application code ---------------------------------------------------
# Changes here invalidate only this layer, not the dependency install
# above.
COPY app ./app

# --- Run as non-root ---------------------------------------------------
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# Matches CATAN_PORT's default in .env.example; override both together
# if you change the published/internal port.
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
