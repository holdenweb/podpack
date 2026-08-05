"""Site navigation, as something apps contribute to rather than a constant.

A hardcoded list of sections in the core package means every new feature needs
an edit to the core package, which is precisely what installing an app is
supposed to avoid. Apps declare their entries on their `SiteApp`; the registry
appends them as it installs; a context processor puts the result in front of
every template.
"""

from dataclasses import dataclass

from flask import current_app


@dataclass(frozen=True)
class Section:
    """One navigation entry. Frozen because apps declare these at import time
    and nothing should be able to rewrite another app's nav after the fact."""

    label: str
    path: str


def sections():
    """The nav entries for the current app, in installation order."""
    return tuple(current_app.extensions["podpack"].nav)
