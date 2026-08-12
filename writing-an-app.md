# Writing a podpack app

Every command and every error message here was produced by running it. The
worked example was built from scratch by following this document; the failures
in [When it goes wrong](#when-it-goes-wrong) were each reproduced deliberately
and then reproduced again independently; and the document was then followed from
scratch a second time by people who had not seen it, which is where about a
dozen of its corrections came from.

**An app is a package exposing one module-level `site_app`.** Everything else —
models, templates, shipped data, configuration, navigation — is convention that
the registry picks up if it is there.

Where to look for what:

| | |
| --- | --- |
| [README.md](README.md) | the reference: what each field means, what the framework guarantees |
| [creating-a-site.md](creating-a-site.md) | the other side of the contract — standing up a site that installs apps |
| **this file** | writing, running, testing and shipping an app |
| [adrs/](adrs/README.md) | why each choice was made, what it cost, what was rejected |
| [claude.md](claude.md) | where the project is up to |

---

## The contract, in one page

```python
# src/podpack_myapp/__init__.py
from podpack import Section, SiteApp

from .views import blueprint          # Blueprint("myapp", __name__, ...)

site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/myapp",              # a request, not a claim
    nav=(Section("My App", "myapp.index"),),   # endpoint, never a path
    init=None,                        # optional callable(app)
)
```

| You provide | podpack does |
| --- | --- |
| `site_app` | imports your package when a site lists it in `apps` |
| `models.py` | imports it at install time, so your tables reach `db.metadata` |
| `templates/<name>/` | searches site → your app → its own defaults |
| `data/` | copies it to the host **once**, the first time on that machine |
| `logging.getLogger(__name__)` | attaches a file handler writing `<name>.log` |
| nothing | creates `<data root>/<name>/` and `<log root>/<name>/` at boot |
| nothing | hands you `[apps.<name>]` through `podpack.app_config()` |

**The app's name is its blueprint's name.** Not the distribution, not the import
name — the blueprint's. It is the template namespace, the directory names, the
config section key, and the `[site.mounts]` key. Naming the blueprint is the
decision that matters; see [ADR-0003](adrs/0003-app-name-is-blueprint-name.md).

---

# Part 1 — building one

The worked example is `podpack-links`, a link roll. It is small, but not a stub:
it has a model, a template, shipped data, per-app configuration, an `init` and a
nav entry, which between them use every part of the registry.

## 1. Start the project

```bash
uv init --lib --name podpack-links podpack-links
cd podpack-links
```

`--lib` is the right template: it gives you `src/podpack_links/`, the `uv_build`
backend and an empty dependency list. It derives the import name from the
distribution name, so `podpack-links` becomes `podpack_links` with nothing to
configure. Only an app whose import name is *not* that needs to say so:

```toml
[tool.uv.build-backend]
module-name = "links"          # for a distribution called podpack-links
```

Two spellings or three is your choice and costs nothing either way; §3 sets out
what each one does.

Delete the `hello()` stub `uv init` writes into `__init__.py`.

## 2. Depend on podpack without depending on it

**An app must not name podpack in `[project.dependencies]`.** This is the one
packaging rule that is neither obvious nor forgiving, and the reason is that a
real dependency's `[tool.uv.sources]` entry *travels with the distribution*: a
site installing your app resolves your sources too.

Measured, with the app installed from git by a site each time:

| Where you declare podpack | Source you give it | The site's `uv lock` |
| --- | --- | --- |
| `[project.dependencies]` | `path` | **fails** — `has no subdirectory ../podpack` |
| `[project.dependencies]` | `git` | **fails** — unless the site names *the same* URL |
| `[dependency-groups]` | either | **locks**, always |

The middle row is the subtle one:

```
  × Failed to resolve dependencies for `theapp` (v0.1.0)
  ╰─▶ Requirements contain conflicting URLs for package `podpack`
```

A site developing against a local podpack checkout — which is exactly what
[creating-a-site.md](creating-a-site.md) Part 1 tells it to do — names a
different source, so it hits that. Your app would work only for sites that
happened to spell podpack the same way you did.

A **dependency group** is development-only metadata: it is not published, so a
consumer never resolves it, and it constrains nobody.

```bash
uv add --dev git+https://github.com/holdenweb/podpack.git
uv add --dev pytest mypy
uv add "flask>=3.0" "sqlalchemy>=2.0"
```

Let `uv add` write these rather than editing `pyproject.toml` by hand: the
source syntax is fiddly, and a command is something you can run and check. It
puts the requirement and its source in the right two places, sorts the group,
and pins a lower bound at whatever it resolved — except for the git
dependency, whose version is the source:

```toml
[dependency-groups]
dev = [
    "mypy>=2.3.0",
    "podpack",
    "pytest>=9.1.1",
]

[tool.uv.sources]
podpack = { git = "https://github.com/holdenweb/podpack.git" }
```

Only the checker configuration is a genuine hand edit, because there is no
command for tool settings:

```toml
[tool.mypy]
files = ["src", "tests"]
```

podpack ships a `py.typed` marker, so an app that annotates its own code needs
nothing beyond that — no `ignore_missing_imports`, which would be a blunt
instrument anyway, suppressing the check for every untyped import rather than
one.

Verified in both directions: with podpack in the dev group a site installing this
app from git locks and syncs cleanly and resolves podpack from *its own* source,
even when the app's dev-group entry names a different one. Move the entry into
`[project.dependencies]` and the site fails as the table says.

Point it at a sibling checkout instead when you are changing the framework and
the app together:

```bash
uv add --dev --editable ../podpack
```

```toml
podpack = { path = "../podpack", editable = true }
```

That binds at lock time, so `uv lock`, `uv sync` — including `--no-dev` — and
`uv run` all fail on any machine without the sibling checkout. The git form
costs the opposite thing: locking needs the podpack remote reachable, and you
test against what is pushed rather than what is on your disk. Syncing from an
existing lock needs neither. Switching between them is just the other `uv add`:
it replaces the entry in `[tool.uv.sources]` and re-syncs the venv, with nothing
to remove first.

> **The trap this replaces.** The older practice is to leave podpack out of the
> project entirely and add it to the venv by hand with
> `uv pip install -e ../podpack`. That works, and `uv run` leaves it alone — but
> `uv sync` is exact and evicts it, because it is not in the lockfile:
>
> ```
> Uninstalled 6 packages in 24ms
>  - podpack==0.2.2 (from file:///Users/sholden/sites/podpack)
> ```
>
> The next test run dies with `ModuleNotFoundError: No module named 'podpack'`,
> which reads like "podpack was never installed" rather than "uv just removed
> it".
>
> **This is a disagreement, not a preference.** `pp-pdf`'s pyproject states the
> rule as "every way of declaring it — an extra, *a dependency group*, a
> `[tool.uv.sources]` path — binds at *lock* time". That is right for a path
> source and wrong for a git one, which is the difference measured above.
> `podpack-notes` follows the same rule; its README's description of how
> (a sibling checkout in `pyproject.toml`) no longer matches its own file.

### Declare what you import, podpack excepted

Your views `import flask`; your models probably `import sqlalchemy`. Declare
both. They arrive through podpack anyway, so nothing breaks if you do not — but
they are on an index, they cannot create the circularity podpack can, and a
package whose metadata does not list what it imports is lying quietly.

```toml
dependencies = ["flask>=3.0", "sqlalchemy>=2.0"]
```

**What you cannot say is which podpack you need.** Rule 1 costs you that: your
published metadata mentions no framework at all, so a site is free to lock an
old podpack against a new app. Put the minimum version in your README and your
release notes, because nothing enforces it.

## 3. Name the blueprint

```python
# src/podpack_links/views.py
blueprint = Blueprint("links", __name__, template_folder="templates")
```

That single string is the app's name, and it reaches further than it looks:

| | |
| --- | --- |
| `podpack-links` | the **distribution** — what a site depends on |
| `podpack_links` | the **import name** — what goes in the site's `apps` list |
| `links` | the **app's name**, from the blueprint — the template namespace, `[apps.links]`, `[site.mounts]`, `<data root>/links/`, `<log root>/links/`, `links.log`, its `flask` CLI group, and the prefix on every endpoint |

Three spellings of one thing. They are not interchangeable, and only the third
is derived from anything: podpack resolves an app during a request through
`request.blueprint`, so the blueprint's name is the app's public identity
whether you meant it to be or not.

Keeping the import name and the app's name deliberately different is fine and
common — `podpack_notes` answers to `notes`. `/_status` reports the mapping
under `installed_from`, so a site owner can look it up rather than guess.

## 4. Views

Ordinary Flask, with three podpack helpers.

```python
# src/podpack_links/views.py
from logging import getLogger

import sqlalchemy as sa
from flask import Blueprint, current_app, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from podpack import db
from podpack.paths import data_dir

from .models import Link

logger = getLogger(__name__)          # writes to <log root>/links/links.log

blueprint = Blueprint("links", __name__, template_folder="templates")

DEFAULT_LIMIT = 20
INTRO_FILE = "intro.md"


@blueprint.route("/")
def index() -> ResponseReturnValue:
    return render_template(
        "links/index.html", title="Links", links=_recent(), intro=_intro()
    )


@blueprint.route("/list")
def list_links() -> ResponseReturnValue:
    return jsonify(links=[link.as_dict() for link in _recent()])


@blueprint.route("/", methods=["POST"])
def add_link() -> ResponseReturnValue:
    payload = request.get_json(silent=True) or {}
    url = payload.get("url", "").strip()
    if not url:
        return jsonify(error="a non-empty 'url' field is required"), 400
    link = Link(url=url, title=payload.get("title", "").strip() or url)
    db.session.add(link)
    db.session.commit()
    logger.info("stored link to %s", url)
    return jsonify(stored=link.as_dict()), 201


def _recent() -> list[Link]:
    """The most recent links, however many this site asked for.

    `init` resolved the limit at boot, so this reads one config key rather than
    reaching for the host config on every request.
    """
    limit = current_app.config["LINKS_LIMIT"]
    return list(db.session.scalars(sa.select(Link).order_by(Link.id.desc()).limit(limit)))


def _intro() -> str | None:
    """Read the shipped intro back from the *host* copy."""
    try:
        return (data_dir() / INTRO_FILE).read_text()
    except OSError:
        return None
```

### Logging is free

`logging.getLogger(__name__)` needs no configuration: podpack attaches a file
handler to your *package's* logger at install time, and records from any module
inside it propagate up on their way to the root. That one line produced

```
2026-08-11 13:25:28,005 INFO [25542] podpack_links.views: stored link to https://podpack.example
```

### Never write a path

`data_dir()` resolves your app's directory under whatever root the site mounted,
so moving it at deployment is a change to the environment and to nothing else:

```python
target = data_dir() / pathlib.Path(name).name    # .name, or a POST can escape
```

`data_dir()` and `app_config()` both default to *the app handling the current
request*, so they only work inside one. Elsewhere — a CLI command, an `init`
hook, a background job — pass your app's name: `data_dir("links")`,
`app_config("links")`. See [When it goes wrong](#when-it-goes-wrong) for the
three different errors that produces, only one of which podpack wrote.

### Never hardcode your own URLs

`url_for("links.index")`, never `/links/`: a site may mount you anywhere, and
everything that goes through `url_for` follows. Outside a request `url_for`
raises before it builds anything, so use `podpack.absolute_url("links.index")`
instead — for a feed, a mail body, a CLI command. It needs `[site] base_url` to
produce a fully-qualified address, which is the site's to set and not yours.

Blueprint static files follow a remount too — **if you gave the blueprint a
`static_folder`**, which the one above does not. With
`Blueprint("links", __name__, template_folder="templates", static_folder="static")`:

| | page | `url_for("links.static", filename="x.css")` |
| --- | --- | --- |
| as the app asked | `/links/` | `/links/static/x.css` |
| remounted by the site | `/moved/` | `/moved/static/x.css` |

Without one there is no `links.static` endpoint and `url_for` raises
`BuildError`. And an app that might be mounted at the site root needs an
explicit `static_url_path`, because `/static` already belongs to the Flask
application and was registered first — see [§10](#10-the-finished-site_app).

### CLI commands

`@blueprint.cli.command("reindex")` gives you `flask links reindex` — the group
is the blueprint's name, which is to say the app's. `cli_group=None` puts it at
the top level as `flask reindex`, sharing a namespace with Flask's own `run`,
`shell` and `routes` and with every other installed app, where a clash resolves
silently in favour of whichever registered first. The default group is the safe
one.

A CLI command runs inside an app context with **no request**, so name your app
in any helper you call there.

### Error pages

`@blueprint.errorhandler(404)` covers a `404` your own views raise; it does
**not** cover a URL under your prefix that matches no rule, because an unmatched
URL is not associated with any blueprint. Verified: `abort(404)` inside an app
view rendered the app's page, while `/nosuchpage` fell through to Flask's
default. Site-wide handlers are the site's to register, not yours.

### Forms, sessions and uploads

podpack gives you `SECRET_KEY`, and therefore `flask.session` and `flash()`. It
gives you nothing else here:

- **No CSRF of any kind.** A plain `<form method="post">` in your app is
  accepted from any origin. Bring `flask-wtf` yourself if you need it, as
  `pp-pdf` does.
- **No cookie policy.** `SESSION_COOKIE_SECURE` and friends are the site's
  business, and no site is obliged to set them.
- **The site caps request size** with `[limits] max_upload_bytes`, which becomes
  `MAX_CONTENT_LENGTH`. You cannot raise it, and you do not get to handle it:
  with the cap at 16 bytes, a 100-byte POST returned **413** the moment the view
  read the body, and an 8-byte one ran normally. Say in your README what your
  app needs.

## 5. Templates

Put them under `templates/<app name>/` and refer to them the same way:

```python
render_template("links/index.html")
```

```html
{% extends "base.html" %}
{% block content %}
{% if intro %}<blockquote>{{ intro }}</blockquote>{% endif %}
<h2>Links</h2>
<ul>{% for link in links %}<li><a href="{{ link.url }}">{{ link.title }}</a></li>{% endfor %}</ul>
{% endblock %}
```

The namespace is what stops two installed apps colliding on `index.html`. The
search order is **site → app → podpack's defaults**
([ADR-0005](adrs/0005-template-resolution-order.md)), so `base.html` resolves to
the site's chrome if it has any and to podpack's if it has not, and your app
never has to know which.

### What is already in the context

podpack's context processor injects two names into **every** template on the
site: `sections`, the assembled navigation, and `site`, the `[site]` table from
the config file. Both are what the chrome renders. Do not pass variables of
those names from a view — you would shadow them for your own page and break the
chrome on it.

`title` is a convention rather than a guarantee: podpack's default chrome uses
it, a site's own need not.

### What you can rely on from a site's chrome

**Less than you would think, and nothing checks it.**
[ADR-0005](adrs/0005-template-resolution-order.md) names `{% block content %}`
as the interface, and that is as declared as it gets. Two measured
consequences:

- **A block the site's chrome does not define is silently discarded.** An app
  overriding `{% block styles %}` — which podpack's default chrome has — against
  a site chrome that does not rendered with the styles simply gone. No error, no
  warning.
- **`flash()` disappears on a site whose chrome does not call
  `get_flashed_messages()`.** podpack's default does; the chrome
  [creating-a-site.md](creating-a-site.md) tells every site author to write does
  not. So your flashes work in your dev site and vanish on the first real one.
  If your app depends on them, say so in your README.

### Do not ship a top-level `base.html`

It is not namespaced, so it is a candidate for every `extends "base.html"` on
the site. On a site that has a package of its own but ships no chrome — which is
every early-stage site — the app's file wins and replaces the site's entire
layout, front page included, and with two such apps installed whichever was
listed first wins. If you want a standalone layout, namespace it
(`templates/links/standalone.html`) and name it explicitly, as `pp-pdf` does.

## 6. Models

```python
# src/podpack_links/models.py
from typing import Any

from podpack import db


class Link(db.Model):
    __tablename__ = "links"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.Text, nullable=False)
    title = db.Column(db.Text, nullable=False)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "url": self.url, "title": self.title}
```

**Defining the class is the whole of model registration.** A `db.Model` subclass
lands on `db.metadata` as an import side effect, and that metadata is what
alembic compares the database against.

So the rule is about *imports*, not about the filename. The registry imports your
package and then `<package>.models` if it exists — but your package imports your
views, and your views import your models, so a model on that chain is registered
whatever you call the file. Verified: renaming `models.py` to `schema.py` and
following the import left all 11 tests passing and `target_metadata()` still
reporting `['links']`.

**What bites is a model module nothing imports** — the second model file you add
and forget to wire up, or one imported lazily inside a function. Then: the site
boots, your pages serve 200, `db.metadata.tables` is `{}`, `target_metadata()`
reports nothing, so the migration never proposes the table either. The bill
arrives at the first query:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: myapp_note
```

`models.py` is the convention because it is the one filename the registry
imports for you, which makes it belt-and-braces rather than load-bearing. Use
it, and import every model into it.

**Name your tables after your app.** Table names are the one identifier in the
whole contract that podpack does *not* namespace: templates, directories, config
sections and endpoints all carry the app's name, while `db.metadata` is flat and
shared across every installed app. So podpack warns you as it installs one:

```
WARNING podpack.registry: app 'alpha' declares the table 'things', which its own
name does not prefix. Table names are shared across every installed app, so a
second app claiming 'things' will stop a site booting.
```

A warning rather than a failure, because a bare name is legal and might be what
you want. But the collision it anticipates is a boot failure for the *site*,
arriving the day somebody installs your app beside another — so it is worth
heeding while you are the one reading the log:

```
RuntimeError: installing 'beta' failed: it declares the table 'things', which
'alpha' already claims. Table names are shared across every installed app --
unlike templates, data directories and config sections, which podpack namespaces
by app name -- so prefix __tablename__ with the app's own name.
```

SQLAlchemy is what actually refuses the second definition, and its
`InvalidRequestError` stays in the chain; podpack catches it to add the two app
names, which the original does not have. Prefix the name — `links_link`, not
`link` — and always set `__tablename__` explicitly. `/_status` reports which app
owns which table.

Name no schema. The application role's `search_path` points at the schema it
owns, so unqualified names land correctly and alembic needs no schema
configuration.

## 7. Shipping data

Anything in `src/podpack_links/data/` is copied into the app's host data
directory when the app is installed — **only if that directory is empty**. The
app then reads the host copy, which is what makes it editable without a rebuild.

```python
(data_dir() / "intro.md").read_text()      # the HOST copy, not the packaged one
```

"Only if empty" means *the first time on this machine*, not every boot — the
same rule as the database bootstrap
([ADR-0008](adrs/0008-shipped-app-data-seeds-once.md)). The consequence,
measured rather than assumed:

```
version 1 installed         -> ['first.txt']
version 2 installed         -> ['first.txt']    <- second.txt never arrives
after deleting the host dir -> ['first.txt', 'second.txt']
```

**If your app ships data that changes with its code, podpack has no answer for
you yet**, and that is a deliberate deferral rather than an oversight. See
[Part 4](#new-versions-and-what-podpack-does-not-do-for-you).

One mechanical detail: **an app that ships data must be a package, not a single
module.** `importlib.resources.files()` on a top-level module resolves to its
*containing directory*, so a stray `data/` sitting beside it is seeded as though
the app shipped it — verified, and the file arrives.

## 8. Configuration, and `init`

A site tunes your app in a table of its own:

```toml
[apps.links]        # keyed by the app's NAME -- its blueprint's -- not the import name
limit = 5
```

Read it with `podpack.app_config()`, which defaults to the app serving the
current request. Read it once at boot if it does not change, which is what
`SiteApp.init` is for — a `callable(app)` run before your blueprint is
registered, with the site's own config already loaded:

```python
# src/podpack_links/__init__.py
def _init(app: Flask) -> None:
    """Settle this app's config keys before the first request.

    `app_config` resolves the app from `request.blueprint` when called with no
    name, and there is no request here, so name it -- and it needs an app
    context either way, which the registry does not push.
    """
    with app.app_context():
        settings = app_config("links")
    app.config["LINKS_LIMIT"] = settings.get("limit", DEFAULT_LIMIT)
```

Both halves of that comment are load-bearing. Drop the `with app.app_context():`
and the site will not boot:

```
RuntimeError: Working outside of application context.
```

Drop the `"links"` argument and it fails one step later, on the missing request.
**The same applies to the site's own `init`** — `create_app` pushes no context
before calling either one.

**Always ship a default.** `[apps.links]` is optional and a site is entitled to
omit it, so `settings.get("limit", DEFAULT_LIMIT)` rather than `settings["limit"]`.

Secrets never go here. Non-secret settings that vary per host live in the config
file; secrets come from the environment
([ADR-0018](adrs/0018-config-in-files-secrets-in-the-environment.md)).

> **The one silent failure in the whole contract.** If the key does not match
> your blueprint's name — because you keyed it on the import name, say —
> `app_config()` returns `{}`. The site boots, the page serves 200, nothing is
> raised and nothing is logged. Measured with warnings armed and logging at
> DEBUG: stderr was zero bytes, while a control run proved the harness would
> have caught anything emitted. Your defaults simply apply for ever. This is
> what the test in [Part 3](#part-3--testing-it) is for.

### What you may assume about the site

The site wires its own extensions — mail, login, session policy — in its own
`init`, which is not an app because two of those register no blueprint and the
third brings its own ([ADR-0025](adrs/0025-the-site-wires-its-own-extensions.md)).
podpack runs the site's `init` **before** any app's, so by the time yours runs
you may read `app.extensions[...]` for a service the site registered.

What you cannot do is *declare* that you need one. There is no mechanism for
"this app requires mail", so check for it and degrade, or say so in your README.

## 9. Navigation

```python
nav=(Section("Links", "links.index"),)
```

**A `Section` names an endpoint, never a path.** The chrome resolves it with
`url_for` as it renders, so the entry follows the app wherever the site mounts
it, with neither side restating anything
([ADR-0022](adrs/0022-nav-is-contributed-and-addressed-by-endpoint.md)).

An entry naming an endpoint no view provides is a boot failure, and deliberately
so — the chrome renders nav on *every* page, so one bad entry would break the
whole site rather than 404 on the page it points at.

Entries appear in the order the site lists the apps, which is the site's choice
and not yours. `podpack.sections()` returns the assembled list if you need it.

## 10. The finished `site_app`

```python
# src/podpack_links/__init__.py
from flask import Flask

from podpack import Section, SiteApp, app_config

from .views import DEFAULT_LIMIT, blueprint


def _init(app: Flask) -> None:
    with app.app_context():
        settings = app_config("links")
    app.config["LINKS_LIMIT"] = settings.get("limit", DEFAULT_LIMIT)


site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/links",
    nav=(Section("Links", "links.index"),),
    init=_init,
)
```

`url_prefix` is **where the app asks to be mounted, and only asks.** A site that
wants you elsewhere says so in `[site.mounts]`, keyed by your app's name, and you
never learn the answer ([ADR-0006](adrs/0006-mount-points-belong-to-the-site.md)).

`None` asks for the site root, which is allowed and is how an app takes the front
page from podpack's fallback
([ADR-0024](adrs/0024-the-front-page-belongs-to-the-site.md)) — but at the root
you also inherit podpack's reserved names. `/healthz`, `/_status` and `/static`
are registered before any app, and Werkzeug matches whichever rule was added
first, so a route of yours at one of those addresses **loses silently**. Measured:
an app routing `/healthz` at the root got podpack's `{"database":"ok","status":"ok"}`.

The finished layout:

```
podpack-links/
├── pyproject.toml
├── devsite.py                       # Part 2
├── devsite/app.toml
├── src/podpack_links/
│   ├── __init__.py                  # site_app, and nothing else
│   ├── views.py                     # Blueprint("links", ...)
│   ├── models.py                    # imported by the registry, not by you
│   ├── data/intro.md                # seeded to the host on first install
│   └── templates/links/index.html   # namespaced
└── tests/
    ├── conftest.py
    └── test_app.py
```

---

# Part 2 — running it

An app cannot be run on a bare Flask app the way a plain blueprint can:
`data_dir()`, `app_config()`, `db` and `base.html` all come from the framework.
The smallest thing that can host your app is a site, so keep a throwaway one in
the repository.

*(An app can be written to need no framework — `pp-pdf` runs both ways, with its
own standalone layout and a test suite that registers its blueprint on a bare
Flask app. That is more work and a second contract to keep; this guide covers
apps written for podpack.)*

```python
# devsite.py -- not part of the package
import os
from pathlib import Path

HERE = Path(__file__).parent
DEV = HERE / "devsite"

# PODPACK_CONFIG is the one variable read at *import* time -- podpack freezes it
# into a module constant -- so it would have to be set before the import below.
# This file passes `config_path=` instead and sets the other three, which are
# read when create_app runs. The import order is convention, not a requirement.
os.environ.setdefault("SECRET_KEY", "dev-only")
os.environ.setdefault("SQLALCHEMY_DATABASE_URI", f"sqlite:///{DEV / 'dev.db'}")
os.environ.setdefault("PODPACK_DATA_ROOT", str(DEV / "data"))
os.environ.setdefault("PODPACK_LOG_ROOT", str(DEV / "logs"))

from flask import Flask  # noqa: E402
from podpack import create_app as _create_site  # noqa: E402
from podpack import db  # noqa: E402


def create_app() -> Flask:
    """A site with nothing installed but this app.

    No `site_package`, so the chrome is podpack's own default -- the honest test
    of whether this app renders on a site that has written none. `create_all`
    rather than alembic: schema authoring belongs to the site, not here.
    """
    app = _create_site(config_path=DEV / "app.toml")
    with app.app_context():
        db.create_all()
    return app
```

```toml
# devsite/app.toml -- the kind of file a site mounts at /etc/holdenweb/app.toml
[site]
name = "links dev site"
environment = "local"
apps = ["podpack_links"]

[apps.links]
limit = 5
```

```bash
uv run flask --app devsite run -p 5001
```

Name the factory `create_app` and the Flask CLI finds it with no `:factory()`
suffix to quote. Add `devsite/data`, `devsite/logs` and `devsite/dev.db` to
`.gitignore`.

`config_path`, `host_config`, `data_root` and `log_root` are `create_app`'s
development and test entry points — in production every one is left unset and
the values come from the config file and the environment. README does not
currently list them; this guide is their documentation.

`/_status` is then the fastest check that your app is wired up properly:

```json
"apps": {"links": {"installed_from": "podpack_links",
                   "url_prefix": "/links",
                   "data_dir": ".../devsite/data/links",
                   "data_dir_writable": true,
                   "log_dir": ".../devsite/logs/links",
                   "log_dir_writable": true,
                   "stored_files": ["intro.md"],
                   "tables": ["links"]}}
```

That one object answers most of what can go wrong: the import-name-to-app-name
mapping, where the site actually mounted you, whether your directories exist and
are writable, whether your shipped data arrived, and which tables in the site's
single `db.metadata` are yours.

Your dev site will not catch everything, and it is worth knowing what it cannot.
It has no `site_package`, so it cannot show you a template hijack or a missing
chrome block, and it uses SQLite rather than PostgreSQL. Build a second one with
a site package and a deliberately minimal `base.html` if either matters to you.

---

# Part 3 — testing it

**Build a real site with your app installed.** Testing the blueprint in
isolation proves your view functions work and says nothing about whether you
have written a well-formed *app*, which is the part that can break.

```python
# tests/conftest.py
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from podpack import create_app, db

# What the `site` fixture hands back: podpack's factory with the test's roots and
# host config already bound in, so a test names only what it is varying.
SiteFactory = Callable[..., Flask]

HOST_CONFIG: dict[str, Any] = {
    "site": {"name": "test site", "environment": "test", "apps": ["podpack_links"]},
    "apps": {"links": {"limit": 3}},
}


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SiteFactory:
    """A podpack site with this app installed, roots pointed at tmp_path.

    Secrets come from the environment in production and `create_app` insists on
    them. The roots are real directories, so the registry's per-app mkdir, data
    seeding and log wiring all run rather than being stubbed.

    The merge is one level deep, so a `host_config` override *replaces* a whole
    top-level table -- override `site` and you must restate `apps` with it.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")

    def _build(**overrides: Any) -> Flask:
        config = {**HOST_CONFIG, **overrides.pop("host_config", {})}
        app = create_app(
            host_config=config,
            data_root=tmp_path / "data",
            log_root=tmp_path / "logs",
            **overrides,
        )
        with app.app_context():
            db.create_all()
        return app

    return _build


@pytest.fixture
def app(site: SiteFactory) -> Flask:
    """The common case. Take `site` instead when a test varies the config."""
    return site()
```

`tests/` has no `__init__.py`, so pytest puts that directory on the path and
tests import from it flat: `from conftest import HOST_CONFIG, SiteFactory`.

Then test the *contract* before the behaviour. These are the ones worth writing,
because each corresponds to something that fails silently or fails everywhere:

```python
def test_the_app_names_itself_after_its_blueprint() -> None:
    assert site_app.name == site_app.blueprint.name == "links"


def test_a_site_installs_it_by_naming_it(site: SiteFactory) -> None:
    """The whole point of the framework: a line of config, not a line of code."""
    app = site()
    assert app.test_client().get("/links/").status_code == 200
    assert app.extensions["podpack"].installed_from == {"links": "podpack_links"}


def test_its_nav_entry_reaches_the_site(app: Flask) -> None:
    assert 'href="/links/"' in app.test_client().get("/").get_data(as_text=True)


def test_the_site_decides_where_it_lands(site: SiteFactory) -> None:
    """`url_prefix` is what this app asks for, not what it is entitled to.

    The whole `site` table is restated because the fixture's merge is shallow.
    """
    app = site(
        host_config={"site": {**HOST_CONFIG["site"], "mounts": {"links": "/elsewhere"}}}
    )
    client = app.test_client()
    assert client.get("/elsewhere/").status_code == 200
    assert client.get("/links/").status_code == 404
    assert 'href="/elsewhere/"' in client.get("/").get_data(as_text=True)


def test_its_limit_comes_from_the_site(site: SiteFactory) -> None:
    """The silent one: a mismatched config key returns {} and nothing is raised."""
    client = site().test_client()
    for n in range(5):
        client.post("/links/", json={"url": f"https://example.com/{n}"})
    assert len(client.get("/links/list").get_json()["links"]) == 3   # not 5, not 20


def test_a_site_without_the_setting_gets_the_packaged_default(site: SiteFactory) -> None:
    app = site(host_config={"apps": {}})
    assert app.config["LINKS_LIMIT"] == DEFAULT_LIMIT


def test_its_tables_reach_alembic(tmp_path: Path) -> None:
    """What a site's migration will actually see -- and no Flask app involved."""
    config = tmp_path / "app.toml"
    config.write_text('[site]\nname = "t"\napps = ["podpack_links"]\n')
    assert "links" in target_metadata(config).tables


def test_its_shipped_data_is_seeded_to_the_host(app: Flask) -> None:
    assert (app.extensions["podpack"].data_root / "links" / "intro.md").is_file()


def test_it_reads_the_host_copy_not_the_packaged_one(app: Flask) -> None:
    intro = app.extensions["podpack"].data_root / "links" / "intro.md"
    intro.write_text("edited on the host")
    assert "edited on the host" in app.test_client().get("/links/").get_data(as_text=True)
```

```bash
uv run pytest
uv run mypy
```

Two testing notes that are easy to get wrong:

- **A test that cannot fail is not evidence.** Check each of these by breaking
  the mechanism it covers and confirming it goes red: mis-key the config
  section, point a `Section` at a missing endpoint, move a model into a module
  nothing imports. *Renaming `models.py` is not one of these* — your views
  import your models, so nothing breaks, and a reader who tries it concludes the
  suite is fine when it has no such coverage at all. `test_its_tables_reach_alembic`
  is the one that goes red.
- **Testing the un-namespaced-`base.html` hijack needs a site package.** A site
  built with no `site_package` *is* podpack, so podpack's own `base.html` is
  found first and an app cannot hijack it however badly it is packaged. The
  hijack only bites on a site that has a package of its own and ships no chrome
  — which is every early-stage site. A test written without `site_package`
  passes for the wrong reason.

---

# Part 4 — shipping it

## Name it `podpack-<app name>`

A convention, not a mechanism: `podpack-notes`, `podpack-links`, in the way
`pytest-*` packages are named. podpack does not scan for the prefix, and
[ADR-0004](adrs/0004-app-list-is-configuration.md) records why that was rejected
rather than deferred.

## What travels

`uv_build` includes everything under your module directory, so templates and
shipped data need no `MANIFEST.in` and no package-data incantation. Check rather
than trust:

```bash
uv build --quiet && unzip -Z1 dist/*.whl
```

```
podpack_links/
podpack_links/__init__.py
podpack_links/data/
podpack_links/data/intro.md
podpack_links/models.py
podpack_links/templates/
podpack_links/templates/links/
podpack_links/templates/links/index.html
podpack_links/views.py
podpack_links-0.2.0.dist-info/
podpack_links-0.2.0.dist-info/WHEEL
podpack_links-0.2.0.dist-info/METADATA
podpack_links-0.2.0.dist-info/RECORD
```

## What a site does with it

Two operations, and only the second is the config-and-restart the framework
advertises:

```bash
# in the site: both packages, because an app does not pull in its framework
uv add git+https://github.com/holdenweb/podpack.git \
       git+https://github.com/holdenweb/podpack-links.git
```

```toml
dependencies = [
    "podpack",
    "podpack-links",
]

[tool.uv.sources]
podpack = { git = "https://github.com/holdenweb/podpack.git" }
podpack-links = { git = "https://github.com/holdenweb/podpack-links.git" }
```

```toml
# the site's config/app.toml
[site]
apps = ["podpack_links"]        # the IMPORT name

[apps.links]                    # the APP's name
limit = 20
```

Putting the distribution in the image is a dependency change and a rebuild;
adding the line to `app.toml` is what enables it. A site deploying in containers
needs every source to be a git URL — a path source cannot survive a build, which
has no such path.

## Migrations are the site's, not yours

**An app cannot ship migrations.** There is one alembic history per site
([ADR-0009](adrs/0009-one-alembic-history.md)), so every site installing your app
generates that app's tables in its own history. What you provide is `models.py`;
what the site runs is `alembic revision --autogenerate`.

Your side of that works without a Flask app at all, which is the point of
[ADR-0010](adrs/0010-migrations-need-no-flask-app.md). Check it from a site that
installs your app:

```bash
env -u SECRET_KEY -u SQLALCHEMY_DATABASE_URI python -c \
  "from podpack.migrations import target_metadata; print(sorted(target_metadata('config/app.toml').tables))"
```

```
['links']
```

So: **when a release of your app changes a model, say so in the release notes.**
The site owner has to autogenerate and apply a revision, and nothing will remind
them. Warn them too that autogenerate must be run with the site's *full* app list
enabled — with an app disabled, alembic faithfully proposes dropping its tables.

## New versions, and what podpack does not do for you

A site takes a new version by upgrading the lock and rebuilding:

```bash
uv lock --upgrade-package podpack-links
```

```
Updated podpack-links v0.1.0 (a6a7e024) -> v0.2.0 (c79305e5)
```

Three gaps to design around, all deferred deliberately:

- **Shipped `data/` seeds once and never again.** A file added in a later
  version never reaches a host that already has the directory. Demonstrated
  in [Part 1](#7-shipping-data).
- **There is no once-only code hook.** `SiteApp.init` runs on every boot, so an
  app needing genuine first-run work — generating a key, building an index — has
  to detect emptiness itself and reimplement the rule.
- **You cannot state a framework floor.** Rule 1 keeps podpack out of your
  metadata, so nothing stops a site pairing your new app with an old podpack.

The trigger for podpack solving the first two is specifically *an app that ships
data expected to change with its code*. If you are about to write one, that is
worth raising before you build around the gap.

---

# When it goes wrong

Every message below was produced by introducing exactly that defect into a
working app, and then reproduced independently by someone who had not seen the
first attempt.

| What you did | When it bites |
| --- | --- |
| No `site_app` in the package | boot |
| A nav `Section` naming an endpoint no view provides | boot |
| `init` calling `app_config()` with no app context | boot |
| Two installed apps sharing a blueprint name | boot |
| `data_dir()` at import time or in `init` | boot |
| A `__tablename__` another installed app already claims | boot, on the site that installs both |
| A model in a module nothing imports | **first query** |
| `[apps.<key>]` not matching the blueprint's name | **never** |
| An un-namespaced `base.html` | **never** |
| A route at `/healthz`, `/_status` or `/static` when mounted at the root | **never** |

### No `site_app`

```
RuntimeError: forgotapp is listed as an installed app but exposes no
module-level `site_app`; see podpack.registry for the contract
```

The message names the *import* name, because with no `SiteApp` to ask, the app's
own name is unknowable. A module that does not exist at all gives you
`ModuleNotFoundError` instead.

Exporting the blueprint under that name — `site_app = blueprint`, the natural
move coming from plain-blueprint packaging — is a different message:

```
RuntimeError: bare_blueprint_app.site_app is a Blueprint, not a SiteApp. A
podpack app wraps its blueprint rather than exporting it: `site_app =
SiteApp(blueprint=..., url_prefix=...)`; see podpack.registry for the contract
```

The migration path holds you to the same contract, and for a reason that is
about the compose stack rather than about migrations: `migrate` runs before
`web` and gates it with `service_completed_successfully`. While `target_metadata()`
accepted a module with no `site_app`, that gate passed and the real failure
surfaced in `web` — one service after the cause. It now fails in `migrate`,
where the cause is.

### A nav entry pointing nowhere

```
RuntimeError: myapp contributes the nav entry 'My App' naming the endpoint
'myapp.home', which no view provides; nav entries name endpoints rather than
paths so that they follow the app wherever the site mounts it
```

### Two apps with the same blueprint name

```
RuntimeError: two installed apps share the blueprint name 'myapp'; it has to be
unique because it decides the template namespace, the data and log directories,
and the config section
```

### A mount for an app that is not installed

```
RuntimeError: [site.mounts] mounts myapp2, which no installed app answers to.
Installed apps: myapp. Note the key is the app's name -- that is, its
blueprint's name -- which is not always the import name listed in `apps`.
```

### `url_prefix` in the app's own config section

```
RuntimeError: [apps.myapp] sets url_prefix, which podpack no longer reads.
Mount points belong to the site, so move it to:

    [site.mounts]
    myapp = "…"

`[apps.myapp]` is for settings the app itself reads.
```

### `data_dir()` or `app_config()` outside a request

Three different errors, and **only the third is podpack's own** — the first two
come from Flask, so searching the message for podpack will not help:

| Where you called it | What you get |
| --- | --- |
| import time, or inside `init` | `RuntimeError: Working outside of application context.` |
| in an app context, no request | `RuntimeError: Working outside of request context.` |
| in a request, but on a route no blueprint owns | `RuntimeError: data_dir()/log_dir() were called outside any blueprint, so there is no app to resolve; pass the app name explicitly` |

`app_config()` raises that third one word for word, because it resolves the app
the same way — so the message names two functions you never called.

Passing the name explicitly fixes the second, not the first: `data_root()`
dereferences `current_app` before the name is ever consulted, so at import time
`data_dir("myapp")` fails exactly as `data_dir()` does. The fix there is not to
resolve paths at import time at all.

### A model in a module nothing imports

Silent at boot. `db.metadata.tables` is `{}`, the pages serve 200, and
`target_metadata()` reports nothing — so the migration does not create the table
either. Then:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: myapp_note
```

### A config key that does not match the blueprint's name

Nothing. `app_config()` returns `{}`, the site boots, the page serves 200 and
your packaged defaults apply for ever. Measured with `warnings.simplefilter("always")`
and logging at DEBUG: stderr was zero bytes.

### An un-namespaced `base.html`

Nothing, and every request returns 200 — but on a site with a site package and no
chrome of its own, the site's entire layout becomes yours:

```
[no site_package]              'APP BASE WINS' on the SITE front page: False
[site_package, no base.html]   'APP BASE WINS' on the SITE front page: True
```

With two such apps installed, install order decides which one wins.

---

# Rules an app must not break

1. **Do not name podpack in `[project.dependencies]`.** A dependency group
   instead.
2. **Do not assume your mount point.** `url_for` inside a request,
   `absolute_url` outside one, never a literal path.
3. **Do not ship a top-level `base.html`.** Namespace every template under your
   app's name.
4. **Do not build paths.** `data_dir()` and `log_dir()`, so the roots stay the
   site's to move.
5. **Do not require `[apps.<name>]`.** Ship a default for every setting.
6. **Do not name a database schema.** The role's `search_path` decides.
7. **Do not resolve anything at import time** that needs an app or a request.
8. **Do not put a model where nothing imports it.** `models.py`, and import
   every model into it.
9. **Do not let your blueprint's name drift** from what the site's config and
   directories are keyed on. It is the app's identity, and nothing checks a
   mismatch for you.
10. **Do not take a bare table name.** `db.metadata` is flat and shared, and a
    clash breaks the *site* that installs both apps. Prefix `__tablename__`
    with your app's name.
11. **Do not shadow `site` or `sections`** in a template context. They are
    injected site-wide and the chrome renders both.

---

# What is still awkward

Honest notes, from building an app rather than imagining one.

- **The template contract is barely declared.** ADR-0005 names
  `{% block content %}` as the interface and stops there. A block the site's
  chrome does not define is silently discarded, and `flash()` disappears
  entirely on a site whose chrome omits `get_flashed_messages()` — which the
  chrome our own site guide tells authors to write does.
- **Nothing warns when a config section matches no installed app.** A mistyped
  `[apps.<name>]` is indistinguishable from a setting the site chose not to
  make. `[site.mounts]` gets this check; `[apps.…]` does not, because a site is
  entitled to configure an app it has not enabled yet.
- **Table names are the one identifier podpack does not namespace**, and it can
  only warn about that rather than prevent it. The clash still lands on the site
  that installed both apps rather than on either author.
- **An app cannot declare what it needs** — neither a podpack version nor a
  service the site wires. Both are README prose and hope.
- **Apps cannot ship migrations**, so every site regenerates your schema in its
  own history. Fine while a schema is stable.
- **An app that ships changing data has no upgrade path**, and neither has one
  that needs first-run code. Both are deferred, not solved.
- **Every app repeats the same `conftest.py`.** The site-building fixture is
  identical across apps and would be better shipped by podpack as a pytest
  plugin than copied.
