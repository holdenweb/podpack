# Deliberately close in shape to the project's top-level Dockerfile: install
# dependencies in their own layer, then the code, then run under a real WSGI
# server rather than Flask's development server.
FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./

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

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --access-logfile - 'app:create_app()'"]
