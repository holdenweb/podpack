"""A small Flask app whose only job is to prove the container plumbing works.

It exercises each of the three things the suite is supposed to get right:

* it reads its non-secret settings from a host-mounted, read-only TOML file,
* it reads its secrets and wiring from the environment,
* it writes both to PostgreSQL and to a host-mounted directory, so you can kill
  the containers and confirm the data is still there afterwards.

The database layer is Flask-SQLAlchemy over psycopg2 -- the same stack the real
holdenweb package uses -- so this is a fair rehearsal of the real thing rather
than a toy with a different driver.
"""

import logging
import os
import pathlib
import tomllib
from datetime import datetime, timezone

import sqlalchemy as sa
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError

CONFIG_PATH = pathlib.Path(os.environ.get("APP_CONFIG", "/etc/holdenweb/app.toml"))
UPLOAD_DIR = pathlib.Path(os.environ.get("APP_UPLOAD_DIR", "/var/lib/holdenweb/uploads"))
LOG_DIR = pathlib.Path(os.environ.get("APP_LOG_DIR", "/var/log/holdenweb"))

db = SQLAlchemy()


class Note(db.Model):
    """A note. Unqualified, so it lands in whatever `search_path` says.

    The bootstrap in db-init/ sets the application role's search_path to the
    `app` schema it owns, so no schema is named here -- exactly as in the real
    application's models.
    """

    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def as_dict(self):
        return {"text": self.text, "created": self.created.isoformat()}


def load_host_config(path=CONFIG_PATH):
    """Read the host-supplied TOML config, failing loudly if it is missing.

    Refusing to start beats starting with silent defaults: a missing file here
    means the bind mount is wrong, which is exactly what this lab exists to
    catch before the same compose file reaches a real host.
    """
    if not path.is_file():
        raise RuntimeError(
            f"host configuration not mounted at {path} -- check the "
            "./config bind mount in compose.yaml"
        )
    with path.open("rb") as stream:
        return tomllib.load(stream)


def require_env(name):
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"required environment variable {name} is not set") from None


def create_app():
    app = Flask(__name__)
    host_config = load_host_config()

    app.config["SECRET_KEY"] = require_env("SECRET_KEY")
    app.config["HOST_CONFIG"] = host_config
    app.config["MAX_CONTENT_LENGTH"] = host_config["limits"]["max_upload_bytes"]

    # Connection details are a secret and come from the environment; how the
    # pool is sized is not, and comes from the host config file.
    database = host_config["database"]
    app.config["SQLALCHEMY_DATABASE_URI"] = require_env("SQLALCHEMY_DATABASE_URI")
    app.config["SQLALCHEMY_ECHO"] = database["echo"]
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": database["pool_size"],
        "max_overflow": database["max_overflow"],
        "pool_pre_ping": database["pool_pre_ping"],
    }
    db.init_app(app)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _add_file_logging(app)
    _create_tables(app)

    @app.get("/healthz")
    def healthz():
        """Liveness *and* readiness: the app is no use without its database."""
        try:
            db.session.execute(sa.text("SELECT 1"))
        except SQLAlchemyError as exc:
            db.session.rollback()
            return jsonify(status="unhealthy", database=str(exc)), 503
        return jsonify(status="ok", database="ok")

    @app.get("/")
    def index():
        """Report where every piece of the app's state actually lives."""
        row = db.session.execute(
            sa.text("SELECT current_database(), current_user, current_schema()")
        ).one()
        return jsonify(
            site=host_config["site"],
            config_source=str(CONFIG_PATH),
            upload_dir=str(UPLOAD_DIR),
            upload_dir_writable=os.access(UPLOAD_DIR, os.W_OK),
            database=row[0],
            database_user=row[1],
            database_schema=row[2],
            note_count=db.session.scalar(sa.select(sa.func.count()).select_from(Note)),
            stored_files=sorted(p.name for p in UPLOAD_DIR.iterdir() if p.is_file()),
        )

    @app.get("/notes")
    def list_notes():
        limit = host_config["limits"]["notes_page_size"]
        notes = db.session.scalars(
            sa.select(Note).order_by(Note.created.desc()).limit(limit)
        )
        return jsonify(notes=[note.as_dict() for note in notes])

    @app.post("/notes")
    def add_note():
        """Persist a note in PostgreSQL, i.e. in the host-mapped data dir."""
        text = (request.get_json(silent=True) or {}).get("text", "").strip()
        if not text:
            return jsonify(error="a non-empty 'text' field is required"), 400
        note = Note(text=text)
        db.session.add(note)
        db.session.commit()
        app.logger.info("stored note of %d characters", len(text))
        return jsonify(stored=note.as_dict(), id=note.id), 201

    @app.post("/uploads/<name>")
    def store_file(name):
        """Persist a file in the host-mapped uploads directory."""
        target = UPLOAD_DIR / pathlib.Path(name).name
        target.write_bytes(request.get_data())
        return jsonify(stored=target.name, bytes=target.stat().st_size), 201

    return app


def _create_tables(app):
    """Create the schema on first boot.

    The real application uses alembic, which is the right answer once there is
    a schema worth migrating. Here the point is only to have somewhere to put a
    row, so create_all is enough -- and it is guarded because gunicorn starts
    several workers at once, and the loser of that race would otherwise crash
    on a table another worker has just created.
    """
    with app.app_context():
        try:
            db.create_all()
        except SQLAlchemyError as exc:
            app.logger.warning("create_all skipped: %s", exc)
            db.session.rollback()


def _add_file_logging(app):
    """Log to the host-mounted log directory as well as to stdout.

    Container stdout is the right default, but production hosts here keep
    their own logs, so this proves the log bind mount is writable too.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_DIR / "app.log")
    except OSError as exc:
        app.logger.warning("file logging disabled: %s", exc)
        return
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(process)d] %(message)s")
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
