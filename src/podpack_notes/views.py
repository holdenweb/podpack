"""Notes: the first podpack app, and the one that proves the mechanism.

It is small on purpose but not a stub -- it has a model, a template, shipped
data, per-app configuration and a nav entry, which between them exercise every
part of the registry that a real app would use.
"""

import pathlib
from logging import getLogger

import sqlalchemy as sa
from flask import Blueprint, jsonify, render_template, request

from podpack import app_config, db
from podpack.paths import data_dir

from .models import Note

logger = getLogger(__name__)

blueprint = Blueprint("notes", __name__, template_folder="templates")

WELCOME_FILE = "welcome.md"


@blueprint.route("/")
def index():
    """The app's own page, rendered in whatever chrome the site provides."""
    return render_template(
        "notes/index.html",
        title="Notes",
        notes=_recent(),
        welcome=_welcome_text(),
    )


@blueprint.route("/list")
def list_notes():
    return jsonify(notes=[note.as_dict() for note in _recent()])


@blueprint.route("/", methods=["POST"])
def add_note():
    """Persist a note, i.e. write to the host-mapped database directory."""
    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        return jsonify(error="a non-empty 'text' field is required"), 400
    note = Note(text=text)
    db.session.add(note)
    db.session.commit()
    logger.info("stored note of %d characters", len(text))
    return jsonify(stored=note.as_dict()), 201


@blueprint.route("/uploads/<name>", methods=["POST"])
def store_file(name):
    """Persist a file in this app's own directory under the host data root.

    The app never learns where that is: `data_dir()` resolves it, so moving the
    root at deployment time is a change to the environment and to nothing else.
    """
    target = data_dir() / pathlib.Path(name).name
    target.write_bytes(request.get_data())
    return jsonify(stored=target.name, bytes=target.stat().st_size), 201


def _recent():
    """The most recent notes, however many this site asks for."""
    limit = app_config().get("page_size", 20)
    return list(db.session.scalars(sa.select(Note).order_by(Note.created.desc()).limit(limit)))


def _welcome_text():
    """Read the shipped welcome note back from the *host* copy.

    The app ships this file in its `data/` directory and the registry seeds it
    to the host on first install. Reading the host copy rather than the packaged
    one is what makes it editable: change it on the host, reload the page, and
    the change is there with no rebuild -- the same property the mounted config
    files have.
    """
    path = data_dir() / WELCOME_FILE
    try:
        return path.read_text()
    except OSError:
        return None
