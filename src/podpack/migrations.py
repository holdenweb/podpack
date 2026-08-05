"""What alembic needs to know about a site, and nothing more.

Migrations are the one place where a plugin system has to reach outside the
running application. Autogenerate compares the database against
`db.metadata`, and a model only reaches that metadata when its module is
imported -- so an app whose models nothing has imported is invisible, and its
tables silently never get created.

Importing every installed app's models is therefore all that alembic needs from
podpack. Note what it deliberately does *not* need: a Flask app, a secret key,
or a working database URI at import time. Keeping the migration environment out
of the application factory means a broken factory is not also a broken
migration.
"""

from .config import installed_apps, load_host_config
from .registry import import_app_models


def target_metadata(config_path=None):
    """The metadata alembic should compare the database against.

    Read this as the honest statement of the footgun it implies: the result
    describes exactly the apps this site currently has enabled. Run autogenerate
    with an app removed from the config file and alembic will faithfully propose
    dropping that app's tables, because from where it is standing they are
    tables no app claims. Django answers this with per-app migration
    directories; podpack has one history, so the answer for now is to autogenerate
    against the full app list.
    """
    from . import db

    host_config = load_host_config(config_path)
    import_app_models(installed_apps(host_config))
    return db.metadata
