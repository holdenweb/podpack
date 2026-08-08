"""The notes app's schema.

Nothing imports this module explicitly. The registry imports it while installing
the app, and defining a `db.Model` subclass registers it on `db.metadata` as an
import side effect -- which is how alembic comes to know about an app's tables
without the migration environment having heard of the app.
"""

from datetime import datetime, timezone
from typing import Any

from podpack import db


class Note(db.Model):  # type: ignore[name-defined]  # flask-sqlalchemy builds db.Model at runtime
    """A note. Unqualified, so it lands in whatever `search_path` says.

    The bootstrap in db-init/ sets the application role's search_path to the
    `app` schema it owns, so no schema is named here -- which is also what keeps
    alembic free of schema configuration.
    """

    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "created": self.created.isoformat()}
