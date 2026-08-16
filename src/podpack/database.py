"""The SQLAlchemy handle, alone in a module so that nothing has to import a
half-built package to reach it.

It lived in `__init__` until login became core (ADR-0033). `auth` defines
models against it and is imported *by* `__init__`, so `from . import db` there
resolved against a package still executing its own body -- which works at
runtime by luck of ordering and which mypy correctly refuses to type.

Every other module may keep importing `podpack.db`; that name still exists and
is this one.
"""

from flask_sqlalchemy import SQLAlchemy

# Created unbound and attached to an app inside create_app(), so that several
# app instances -- one per test, say -- can coexist safely.
db = SQLAlchemy()
