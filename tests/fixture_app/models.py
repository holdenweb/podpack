"""A model, so that installing this app puts a table on db.metadata.

Nothing imports this: the registry does, at install time, and defining the class
is what registers it.
"""

from podpack import db


class Widget(db.Model):  # type: ignore[name-defined]
    __tablename__ = "widgets"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.Text, nullable=False)
