# Build the site from the podpack distribution rather than from a loose
# directory of source: the image then contains exactly what `uv sync --frozen`
# resolves from the committed lockfile, which is the same thing a developer
# gets locally and the same thing a deployment gets.
FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv is copied from its own pinned image rather than pip-installed, so the
# build has one fewer network fetch to go wrong and one fewer version to drift.
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, in their own layer keyed on the lockfile, so that editing
# the source does not reinstall the world on every rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
COPY README.md ./
RUN uv sync --frozen --no-dev

# The migration environment ships in the image because the `migrate` service
# runs from it: the schema a build expects and the code that expects it then
# travel together and cannot be deployed out of step.
COPY alembic.ini ./
COPY alembic/ ./alembic/

# The container healthcheck runs this. It is a file rather than a `python -c`
# one-liner because podman splits ["CMD", ...] arguments on whitespace; see the
# module docstring.
COPY container/healthcheck.py ./healthcheck.py

ENV PATH="/app/.venv/bin:${PATH}"

# Run unprivileged. The uid/gid are fixed so the compose init-storage service
# knows who to hand the bind-mounted host directories to.
#
# The home directory is not decoration: gunicorn's control server puts its
# socket under $HOME and refuses to start quietly if it cannot write there.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app
ENV HOME=/home/app
USER 10001:10001

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --access-logfile - 'podpack:create_app()'"]
