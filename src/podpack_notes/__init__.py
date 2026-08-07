"""The notes app.

A podpack app is a package exposing one module-level `site_app`. Everything else
about it is convention: `models.py` for its schema, `templates/<name>/` for its
templates, `data/` for anything it ships.
"""

from podpack import Section, SiteApp

from .views import blueprint

site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/notes",
    nav=(Section("Notes", "notes.index"),),
)
