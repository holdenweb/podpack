"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Unconditional, and needed more often than it looks. Autogenerate renders the
# `permissions` column of the login tables as
# `flask_security.datastore.AsaList()` and does *not* emit an import for it, so
# a revision touching those tables dies with `NameError: name 'flask_security'
# is not defined` -- at migrate time, on a deployment, rather than when it was
# written. Every podpack site has those tables since ADR-0033 and every one has
# the dependency, so the honest fix is to import it always and let the noqa
# carry the revisions that do not need it.
import flask_security.datastore  # noqa: F401
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
