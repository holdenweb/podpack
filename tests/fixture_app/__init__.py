"""A throwaway app, existing only so podpack can test its own registry.

Deliberately *not* podpack_notes, which now lives in its own repository. A
framework whose test suite installs a real app can only be tested where that app
is also checked out, and makes a framework bug and an app bug harder to tell
apart.

Its import name and its own name differ on purpose -- `fixture_app` is imported
and it answers to `widget` -- because that distinction is the one most easily
got wrong, and several tests turn on it.
"""

from podpack import Section, SiteApp

from .views import blueprint

site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/widget",
    nav=(Section("Widget", "widget.index"),),
)
