"""What the registry promises, tested rather than assumed.

Each of these corresponds to something a Django app gets for free and Flask does
not, so a failure here means the plugin mechanism has stopped being a plugin
mechanism.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from flask import Blueprint, Flask, url_for
from flask.testing import FlaskClient
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError

from conftest import SiteFactory

from podpack import Section, SiteApp, absolute_url, app_config, create_app, db
from podpack.paths import data_dir, unclaimed


def test_app_list_is_configuration_not_code(site: SiteFactory) -> None:
    """Installing an app must be an edit to the config file and nothing else."""
    with_widget = site()
    without = site(host_config={"site": {"name": "bare", "environment": "test", "apps": []}})

    assert "widget.index" in with_widget.view_functions
    assert "widget.index" not in without.view_functions
    assert without.extensions["podpack"].nav == []


def test_models_reach_metadata(app: Flask) -> None:
    """The claim alembic depends on.

    Autogenerate reads `db.metadata` after building an app, so an installed
    app's tables have to be registered by the act of installing it. If this
    fails, migrations silently stop seeing the app.
    """
    assert "widgets" in db.metadata.tables


def test_nav_is_contributed_by_apps(app: Flask, client: FlaskClient) -> None:
    assert app.extensions["podpack"].nav == [Section("Widget", "widget.index")]
    body = client.get("/").get_data(as_text=True)
    # Twice over: the header nav in base.html, and the installed-apps list in
    # index.html. Both are checked because an empty href is what a template
    # reading the wrong attribute off a Section produces -- Jinja renders an
    # Undefined as "" and says nothing -- so a link that silently goes nowhere
    # is the failure this pair is here to catch.
    assert body.count('href="/widget/"') == 2
    assert 'href=""' not in body


def test_site_can_mount_an_app_where_it_likes(site: SiteFactory) -> None:
    """An app's `url_prefix` is what it asks for, not what it gets.

    The app list decides *whether* a feature is installed; the shape of the
    site's address space is still the site's to choose. Without this, adding an
    app would mean accepting whatever URL its author happened to pick.
    """
    app = site(
        host_config={
            "site": {
                "name": "test site",
                "environment": "test",
                "apps": ["fixture_app"],
                "mounts": {"widget": "/writing/widget"},
            }
        }
    )
    client = app.test_client()

    assert client.get("/writing/widget/").status_code == 200
    assert client.get("/widget/").status_code == 404
    # The nav follows without the app or the site restating anything, which is
    # the whole reason a Section holds an endpoint rather than a path.
    assert 'href="/writing/widget/"' in client.get("/").get_data(as_text=True)
    # And what /_status reports is where the app actually ended up.
    assert app.extensions["podpack"].apps["widget"].url_prefix == "/writing/widget"


FRONT_PAGE_APP = '''
from flask import Blueprint, url_for
from podpack import SiteApp

blueprint = Blueprint("front", __name__)


@blueprint.route("/")
def index() -> str:
    return "THE SITE'S OWN FRONT PAGE"


site_app = SiteApp(blueprint=blueprint, url_prefix=None)
'''


def test_an_app_may_claim_the_site_root(site: SiteFactory, app_package: Callable[[str, str], str]) -> None:
    """`/` belongs to the site, not to the framework.

    podpack serves a default front page so that a site with no apps shows
    something rather than 404. It used to register that unconditionally, which
    meant an app routing `/` lost silently -- both rules existed and Werkzeug
    matched whichever was added first, which was always podpack's.
    """
    app_package("front_page_app", FRONT_PAGE_APP)
    app = site(
        host_config={
            "site": {"name": "s", "environment": "test", "apps": ["front_page_app"]}
        }
    )

    assert app.test_client().get("/").get_data(as_text=True) == "THE SITE'S OWN FRONT PAGE"
    assert [r.endpoint for r in app.url_map.iter_rules() if str(r.rule) == "/"] == ["front.index"]


def test_the_default_front_page_is_there_when_nothing_claims_it(site: SiteFactory) -> None:
    """A site with no apps is a valid site and should not 404 on its own root."""
    app = site(host_config={"site": {"name": "bare", "environment": "test", "apps": []}})

    body = app.test_client().get("/").get_data(as_text=True)
    assert "No apps are installed yet" in body


ORDER_APP = '''
from flask import Blueprint, Flask
from podpack import SiteApp

blueprint = Blueprint("ordered", __name__)


def _init(app: Flask) -> None:
    # Relies on a service the *site* set up. If the site's init has not run,
    # this raises and the ordering guarantee is broken.
    app.config["ORDER"] = app.config["SITE_SERVICE"] + ",app"


site_app = SiteApp(blueprint=blueprint, url_prefix="/ordered", init=_init)
'''


def test_the_site_wires_its_own_extensions(site: SiteFactory) -> None:
    """Mail, login and session policy belong to the site, not to any feature.

    They are not apps: two of the three register no blueprint at all and the
    third brings its own, so a `SiteApp` shim would mean inventing a blueprint
    to satisfy a contract built around having one.
    """
    seen = {}

    def wire(app: Flask) -> None:
        seen["config_available"] = app.config["SECRET_KEY"] == "test-secret-key"
        seen["state_available"] = "podpack" in app.extensions
        app.config["SITE_SERVICE"] = "site"

    app = site(init=wire)

    assert seen == {"config_available": True, "state_available": True}
    assert app.config["SITE_SERVICE"] == "site"


def test_the_site_is_wired_before_its_apps(site: SiteFactory, app_package: Callable[[str, str], str]) -> None:
    """An app that sends mail should not have to care whether mail is ready."""
    app_package("ordered_app", ORDER_APP)
    app = site(
        init=lambda a: a.config.__setitem__("SITE_SERVICE", "site"),
        host_config={
            "site": {"name": "s", "environment": "test", "apps": ["ordered_app"]}
        },
    )
    assert app.config["ORDER"] == "site,app"


def test_absolute_url_works_outside_a_request(site: SiteFactory) -> None:
    """The gap `base_url` exists to fill.

    Inside a request Flask builds an external URL from the `Host` header and
    needs no configuration. Outside one it raises, which is where mail from a
    job, a feed, or a CLI command lands.
    """
    app = site(
        host_config={
            "site": {
                "name": "s",
                "environment": "test",
                "apps": ["fixture_app"],
                "base_url": "https://example.com",
            }
        }
    )
    with app.app_context():
        assert absolute_url("widget.index") == "https://example.com/widget/"
        with pytest.raises(RuntimeError):
            url_for("widget.index", _external=True)


def test_absolute_url_falls_back_to_the_request(site: SiteFactory) -> None:
    """A site need not set `base_url`; in a request Flask already knows."""
    app = site()
    with app.test_request_context("/", base_url="https://asked-for.example"):
        assert absolute_url("widget.index") == "https://asked-for.example/widget/"


def test_a_base_url_that_cannot_be_joined_to_is_a_boot_failure(site: SiteFactory) -> None:
    """`urljoin('example.com', '/x')` is `/x` -- a link nothing can follow.

    Silently useless is the worst outcome for a value whose only job is to be
    pasted into mail, so the scheme-less case is caught at boot instead.
    """
    with pytest.raises(RuntimeError, match="no scheme and host"):
        site(
            host_config={
                "site": {
                    "name": "s",
                    "environment": "test",
                    "apps": [],
                    "base_url": "example.com",
                }
            }
        )


def test_status_works_on_any_dialect(client: FlaskClient) -> None:
    """The diagnostic must work before everything is right, not after.

    It asks the server which database, role and schema the connection became --
    PostgreSQL functions, so on SQLite the query fails. It used to take the
    whole route down with it, which made /_status a 500 for the entire local
    development half of the documented workflow.
    """
    body = client.get("/_status").get_json()
    assert body["database_schema"].startswith("(not reported by sqlite")
    # ...and the parts that do not depend on the dialect are all still there.
    assert body["apps"]["widget"]["data_dir_writable"] is True
    assert body["unclaimed"]["data"] == []
    assert body["unclaimed"]["logs"] == []
    # `tables` is not asserted exactly here, and cannot be: `db.metadata` belongs
    # to the process rather than to the site, so what `db.create_all()`
    # materialises depends on which test modules have been imported by now. A
    # deployment imports one site's apps and has no such ambiguity; the tests
    # below assert membership for that reason.
    assert isinstance(body["unclaimed"]["tables"], list)


def test_data_left_by_an_uninstalled_app_is_reported(app: Flask) -> None:
    """Uninstalling keeps an app's data, so something has to say it is there.

    Removing an app from `apps` deliberately does not delete what it was
    holding. Without this, the disk and `/_status` disagree about what the site
    consists of, and the difference is invisible until somebody goes looking on
    the host.
    """
    state = app.extensions["podpack"]
    assert unclaimed(state.data_root, state.apps) == []

    # An app that used to be installed, and the data it left behind.
    (state.data_root / "retired_app").mkdir()
    (state.data_root / "stray.txt").write_text("not an app's directory either")

    assert unclaimed(state.data_root, state.apps) == ["retired_app", "stray.txt"]
    # ...and the installed app is still not mistaken for one of them.
    assert "widget" not in unclaimed(state.data_root, state.apps)


def test_unclaimed_survives_a_root_that_does_not_exist(tmp_path: Path) -> None:
    """A site with no apps installed never creates a root at all."""
    assert unclaimed(tmp_path / "never-made", {}) == []


def test_the_import_name_is_recorded_for_reporting(app: Flask) -> None:
    """An app's import name and its own name differ routinely, and it matters.

    `apps` lists the import name; `[site.mounts]`, `[apps.<name>]` and the data
    directories are all keyed by the app's own name. Keeping the mapping is what
    lets `/_status` show it, so a site can look the answer up rather than
    discover it from a boot failure.
    """
    assert app.extensions["podpack"].installed_from == {"widget": "fixture_app"}


def test_mounting_is_not_visible_to_the_app(site: SiteFactory) -> None:
    """Where an app is mounted is the site's business, not the app's.

    The two used to share `[apps.<name>]`, so `app_config()` handed the app the
    site's `url_prefix` alongside its own settings -- a decision it has no part
    in, presented as though it were one of its own.
    """
    app = site(
        host_config={
            "site": {
                "name": "test site",
                "environment": "test",
                "apps": ["fixture_app"],
                "mounts": {"widget": "/writing/widget"},
            }
        }
    )
    with app.test_request_context("/writing/widget/"):
        assert app_config() == {"size": 5}


def test_mounting_an_app_that_is_not_installed_is_a_boot_failure(site: SiteFactory) -> None:
    """The one thing a separate table costs: it can drift from the app list.

    Silently ignoring the stray entry would leave the app at the address it
    asked for -- exactly the address the site said it did not want.
    """
    with pytest.raises(RuntimeError, match="no installed app answers to"):
        site(
            host_config={
                "site": {
                    "name": "x",
                    "environment": "test",
                    "apps": ["fixture_app"],
                    "mounts": {"widgetz": "/typo"},
                }
            }
        )


def test_the_old_spelling_is_rejected_rather_than_ignored(site: SiteFactory) -> None:
    """`url_prefix` in the app's own table used to be how this was configured.

    Quietly ignoring it would downgrade a site that had not been updated, with
    no indication that its chosen address had stopped taking effect.
    """
    with pytest.raises(RuntimeError, match=r"\[site.mounts\]"):
        site(host_config={"apps": {"widget": {"url_prefix": "/writing/widget"}}})


def test_nav_naming_an_unknown_endpoint_is_a_boot_failure(site: SiteFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Better than a link that only fails when somebody clicks it.

    A bad nav endpoint breaks `url_for` in the chrome, so it would take out
    every page on the site rather than the one it points at.
    """
    import fixture_app

    monkeypatch.setattr(
        fixture_app,
        "site_app",
        replace(fixture_app.site_app, nav=(Section("Widget", "widget.nope"),)),
    )
    with pytest.raises(RuntimeError, match="widget.nope"):
        site()


def test_app_template_is_namespaced_and_used(client: FlaskClient) -> None:
    """`widget/index.html` must resolve to the app's own copy."""
    response = client.get("/widget/")
    assert response.status_code == 200
    assert "<h2>Widget</h2>" in response.get_data(as_text=True)


def test_app_renders_on_a_site_with_no_chrome(site: SiteFactory, site_package: Callable[[str, dict[str, str]], str]) -> None:
    """An app must render against a site that ships no base.html of its own.

    This is the one that actually exercises the fallback loader: the site
    package below has a template directory but no `base.html`, and the fixture app's
    template extends one. Without podpack's loader appended *after* the
    blueprints, this raises TemplateNotFound.
    """
    site_package("bare_site", {})
    app = site(site_package="bare_site")
    body = app.test_client().get("/widget/").get_data(as_text=True)

    assert "<h2>Widget</h2>" in body
    assert "Served by podpack" in body  # podpack's default chrome


def test_site_can_override_an_app_template(site: SiteFactory, site_package: Callable[[str, dict[str, str]], str]) -> None:
    """A site overrides an app's template by shipping the same namespaced path.

    This precedence is Flask's own ordering rather than anything podpack adds,
    but the whole template story rests on it, so it is worth a test that would
    catch the loader wiring breaking it.
    """
    site_package("mysite", {"widget/index.html": "OVERRIDDEN"})
    app = site(site_package="mysite")

    assert app.test_client().get("/widget/").get_data(as_text=True) == "OVERRIDDEN"


def test_per_app_directories_are_created(app: Flask) -> None:
    state = app.extensions["podpack"]
    assert (state.data_root / "widget").is_dir()
    assert (state.log_root / "widget").is_dir()


def test_shipped_data_is_seeded_once(app: Flask, site: SiteFactory) -> None:
    """Seeding follows the db-init rule: first time on this machine, not every
    restart. Re-arming means deleting the app's host data directory."""
    welcome = app.extensions["podpack"].data_root / "widget" / "seed.txt"
    assert welcome.is_file()

    welcome.write_text("edited on the host")
    site()  # a restart: same roots, app installed again
    assert welcome.read_text() == "edited on the host"

    welcome.unlink()
    site()  # still not empty? it is now, so seeding re-arms
    assert "shipped with the fixture app" in welcome.read_text()


def test_seeded_data_is_read_from_the_host_copy(app: Flask, client: FlaskClient) -> None:
    """Editing the host copy must change the page, with no rebuild."""
    seeded = app.extensions["podpack"].data_root / "widget" / "seed.txt"
    seeded.write_text("host-side edit")
    # /widget/seeded reads the host copy, which is the whole point: an app runs
    # against the seeded file on disk, not the one inside its own package.
    assert client.get("/widget/seeded").get_data(as_text=True) == "host-side edit"


def test_app_config_is_namespaced(client: FlaskClient, app: Flask) -> None:
    """An app reads its own section of the host config and no one else's."""
    from podpack import app_config

    with app.test_request_context("/widget/"):
        assert app_config().get("size") == 5
        assert app_config("widget") == {"size": 5}


def test_app_name_is_its_blueprint_name(app: Flask) -> None:
    """One name, so it cannot be two names that disagree.

    `data_dir()` and `app_config()` resolve the app from `request.blueprint`,
    while the registry creates directories and reads config from
    `site_app.name`. When those were separate fields, a mismatch made the
    registry prepare one directory and the views use another, and left
    `app_config()` returning an empty dict -- with nothing raised at boot or in
    the request. Deriving the name is what makes that unrepresentable.
    """
    from fixture_app import site_app

    assert site_app.name == site_app.blueprint.name == "widget"

    state = app.extensions["podpack"]
    with app.test_request_context("/widget/"):
        assert data_dir() == state.data_root / "widget"
        assert app_config() == {"size": 5}


def test_site_app_takes_no_name_of_its_own(site: SiteFactory) -> None:
    """Declaring a name separately is now an error rather than a hazard."""
    with pytest.raises(TypeError):
        # mypy flags this too, which is the decision holding from a second
        # direction: the name is derived, so there is no argument to pass.
        SiteApp(name="mismatched", blueprint=Blueprint("bp", __name__))  # type: ignore[call-arg]


def test_migration_metadata_needs_no_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The migration environment must not need a Flask app, or its secrets.

    Building the target metadata deliberately does not call `create_app`, so
    that a broken factory is not also a broken migration. Deleting both
    variables `create_app` requires is what proves the independence: if this
    ever starts building an app, it will fail here rather than in production.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)

    config = tmp_path / "app.toml"
    config.write_text('[site]\nname = "x"\napps = ["fixture_app"]\n')

    from podpack.migrations import target_metadata

    assert "widgets" in target_metadata(config).tables


def test_unknown_app_is_a_boot_failure(site: SiteFactory) -> None:
    with pytest.raises(ModuleNotFoundError):
        site(host_config={"site": {"name": "x", "environment": "test", "apps": ["no_such_app"]}})


def test_module_without_site_app_is_rejected(site: SiteFactory) -> None:
    """A clear error beats a site that boots with a feature silently missing."""
    with pytest.raises(RuntimeError, match="no module-level"):
        site(host_config={"site": {"name": "x", "environment": "test", "apps": ["json"]}})


BARE_BLUEPRINT_APP = '''
from flask import Blueprint

blueprint = Blueprint("bare", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


site_app = blueprint          # the plain-blueprint packaging, not podpack's
'''


def test_a_bare_blueprint_is_named_as_such(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    """The wrong type and the missing name are different mistakes.

    `pp-pdf` shows why this one is worth distinguishing: a package can be usable
    both as a plain blueprint and as a podpack app, so exporting the blueprint
    under this name is a natural half-step rather than an exotic error. While
    both cases shared a message, it told an author whose module said `site_app =
    blueprint` that the module exposed no `site_app`.
    """
    app_package("bare_blueprint_app", BARE_BLUEPRINT_APP)
    with pytest.raises(RuntimeError, match="is a Blueprint, not a SiteApp"):
        site(
            host_config={
                "site": {"name": "x", "environment": "test", "apps": ["bare_blueprint_app"]}
            }
        )


def test_the_migration_environment_holds_apps_to_the_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Because `migrate` gates `web`, and a gate that passes blames the wrong thing.

    A module with no `site_app` used to sail through here, so the compose stack's
    one-shot `migrate` service completed successfully, satisfied
    `service_completed_successfully`, and the site failed in `web` -- one service
    after the cause. Checked without a Flask app, so ADR-0010 still holds, which
    is what deleting the secrets below is for.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)

    config = tmp_path / "app.toml"
    config.write_text('[site]\nname = "x"\napps = ["json"]\n')

    from podpack.migrations import target_metadata

    with pytest.raises(RuntimeError, match="no module-level"):
        target_metadata(config)


CLASHING_APP = '''
from flask import Blueprint
from podpack import SiteApp, db


class Thing(db.Model):
    __tablename__ = "shared_table_name"
    id = db.Column(db.Integer, primary_key=True)


blueprint = Blueprint("{name}", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


site_app = SiteApp(blueprint=blueprint, url_prefix="/{name}")
'''


def test_a_table_name_clash_names_both_apps(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    """SQLAlchemy names the table; podpack has to supply the two apps.

    Table names are the one identifier podpack does not namespace, so this is
    the one collision it cannot prevent -- only explain. The site owner hitting
    it installed two apps written by people who never met.
    """
    app_package("clash_first", CLASHING_APP.format(name="clashfirst"))
    app_package("clash_second", CLASHING_APP.format(name="clashsecond"))

    with pytest.raises(RuntimeError) as caught:
        site(
            host_config={
                "site": {
                    "name": "x",
                    "environment": "test",
                    "apps": ["clash_first", "clash_second"],
                }
            }
        )

    message = str(caught.value)
    assert "clash_second" in message          # the one being installed
    assert "clashfirst" in message            # the one that already claimed it
    assert "shared_table_name" in message
    # SQLAlchemy's own error stays in the chain rather than being swallowed.
    assert isinstance(caught.value.__cause__, InvalidRequestError)


UNNAMESPACED_APP = '''
from flask import Blueprint
from podpack import SiteApp, db


class Thing(db.Model):
    __tablename__ = "borrowed_noun"
    id = db.Column(db.Integer, primary_key=True)


blueprint = Blueprint("tidyapp", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


site_app = SiteApp(blueprint=blueprint, url_prefix="/tidyapp")
'''


def test_an_unnamespaced_table_name_is_warned_about(
    site: SiteFactory,
    app_package: Callable[[str, str], str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A warning and not a failure, because the name is legal and may be wanted.

    It fires while the author is watching. The collision it anticipates happens
    on a site that installs two apps, which is the one moment nobody who can fix
    it is in the room.
    """
    app_package("tidy_app", UNNAMESPACED_APP)
    with caplog.at_level(logging.WARNING, logger="podpack.registry"):
        site(host_config={"site": {"name": "x", "environment": "test", "apps": ["tidy_app"]}})

    assert "borrowed_noun" in caplog.text
    assert "tidyapp" in caplog.text


def test_a_namespaced_table_name_is_not_warned_about(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    """The teeth on the one above: the fixture app's `widgets` starts with `widget`."""
    assert app.extensions["podpack"].table_owners["widgets"] == "widget"
    assert "widgets" not in caplog.text


def _claiming_app(tag: str) -> str:
    """Source for an app that claims both its own table and one it cannot see.

    Names are varied per test on purpose. `db.metadata` belongs to the process,
    and the `app_package` fixture drops the module from `sys.modules` at
    teardown -- so a second test importing the same source re-executes the model
    definition against metadata that already holds its table, and fails with the
    clash this suite raises deliberately elsewhere.
    """
    return f'''
from flask import Blueprint
from podpack import SiteApp, db


class Claimed(db.Model):
    __tablename__ = "claimed_noun_{tag}"
    id = db.Column(db.Integer, primary_key=True)


# No model and so no mapper, which is what makes it invisible to attribution by
# defining module -- the shape of flask-security's `roles_users`.
association = db.Table(
    "built_elsewhere_{tag}",
    db.Column("left_id", db.Integer),
    db.Column("right_id", db.Integer),
)

blueprint = Blueprint("claimer_{tag}", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


site_app = SiteApp(
    blueprint=blueprint,
    url_prefix="/claimer",
    owns_tables=frozenset({{"claimed_noun_{tag}", "built_elsewhere_{tag}"}}),
)
'''


def _install_only(site: SiteFactory, name: str, **overrides: Any) -> Flask:
    """Build a site whose app list is exactly one named app."""
    return site(
        host_config={"site": {"name": "x", "environment": "test", "apps": [name]}},
        **overrides,
    )


def test_a_claimed_table_name_is_not_warned_about(
    site: SiteFactory,
    app_package: Callable[[str, str], str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Some names are not the app's to choose.

    flask-security derives `user` and `role` from its own mixins, and its
    datastore, its documentation and its join table all assume them. Saying so
    is what distinguishes a deliberate name from the accident the warning is
    for.
    """
    app_package("claiming_app_quiet", _claiming_app("quiet"))
    with caplog.at_level(logging.WARNING, logger="podpack.registry"):
        _install_only(site, "claiming_app_quiet")

    assert "claimed_noun_quiet" not in caplog.text


def test_claiming_a_table_records_who_owns_it(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    """A declaration, not a mute -- which is the whole reason to prefer it.

    `built_elsewhere` has no mapper, so attribution by defining module cannot
    see it at all; without the claim nothing in the site knows who answers for
    it. That is the `roles_users` hole, closed.
    """
    app_package("claiming_app_owned", _claiming_app("owned"))
    owners = _install_only(site, "claiming_app_owned").extensions["podpack"].table_owners
    assert owners["claimed_noun_owned"] == "claimer_owned"
    assert owners["built_elsewhere_owned"] == "claimer_owned"


def test_a_table_no_app_answers_for_is_reported(
    app: Flask, client: FlaskClient
) -> None:
    """The same question `unclaimed` asks of the roots, asked of the database.

    A table outlives the app removed from `apps`, exactly as its data directory
    does -- deliberately, because uninstalling a feature must not destroy what
    it was holding. Reported so the schema and `/_status` cannot quietly
    disagree about what the site consists of.
    """
    with app.app_context():
        db.session.execute(sa.text("CREATE TABLE retired_apps_leftovers (id INTEGER)"))
        db.session.commit()

    tables = client.get("/_status").get_json()["unclaimed"]["tables"]
    assert "retired_apps_leftovers" in tables
    # The installed app's own table is answered for, so it is not in the list.
    assert "widgets" not in tables


def test_alembics_own_bookkeeping_is_not_unclaimed(
    app: Flask, client: FlaskClient
) -> None:
    """It belongs to the migration history rather than to any app, and reporting
    it every time would train the reader to ignore the field."""
    with app.app_context():
        db.session.execute(sa.text("CREATE TABLE alembic_version (version_num TEXT)"))
        db.session.commit()

    assert "alembic_version" not in client.get("/_status").get_json()["unclaimed"]["tables"]


def test_unclaimed_tables_survive_a_database_that_cannot_be_read(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic that only works once everything is right is no diagnostic.

    The same rule `_database_identity` already follows: the route reporting the
    mounts and grants is the one an operator reaches for when something is
    already wrong, so a failure here is a sentence in the report rather than a
    500 that hides the rest of it.
    """
    def unreadable(_engine: object) -> object:
        raise SQLAlchemyError("no route to host")

    monkeypatch.setattr("podpack.core.sa.inspect", unreadable)
    body = client.get("/_status").get_json()
    assert body["unclaimed"]["tables"] == "(not reported: SQLAlchemyError)"
    # ...and everything that does not depend on the database is still there.
    assert body["apps"]["widget"]["data_dir_writable"] is True


REPORTING_APP = '''
from flask import Blueprint
from podpack import Health, Section, SiteApp

blueprint = Blueprint("reporter", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


class Reporter(SiteApp):
    def healthz(self):
        return Health(ok=STATE["ok"], detail=STATE["detail"], fatal=STATE["fatal"])

    def status(self):
        return {"queue_depth": 3}


STATE = {"ok": True, "detail": "", "fatal": False}
site_app = Reporter(blueprint=blueprint, url_prefix="/reporter")
'''

BROKEN_REPORTER = '''
from flask import Blueprint
from podpack import SiteApp

blueprint = Blueprint("broken", __name__)


@blueprint.route("/")
def index() -> str:
    return "hi"


class Broken(SiteApp):
    def healthz(self):
        raise RuntimeError("the check itself is broken")

    def status(self):
        raise RuntimeError("so is this one")


site_app = Broken(blueprint=blueprint, url_prefix="/broken")
'''


def _reporting_site(site: SiteFactory, app_package, source: str, name: str) -> Flask:
    app_package(name, source)
    return site(host_config={"site": {"name": "x", "environment": "test", "apps": [name]}})


def test_an_app_reports_its_own_health_and_status(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    app = _reporting_site(site, app_package, REPORTING_APP, "reporting_app")
    client = app.test_client()

    health = client.get("/healthz").get_json()
    assert health["status"] == "ok"
    assert health["apps"]["reporter"]["status"] == "ok"
    # How long the check took, so a slow one is visible rather than mysterious.
    assert isinstance(health["apps"]["reporter"]["ms"], (int, float))

    status = client.get("/_status").get_json()
    assert status["apps"]["reporter"]["reported"] == {"queue_depth": 3}


def test_an_unhealthy_app_does_not_by_itself_fail_the_site(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    """The site keeps serving its other features, and says what is wrong.

    /healthz gates the whole stack through the container healthcheck, so the
    default has to be report-not-fail.
    """
    app = _reporting_site(site, app_package, REPORTING_APP, "reporting_app")
    import reporting_app

    reporting_app.STATE.update(ok=False, detail="store unreachable")
    response = app.test_client().get("/healthz")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["apps"]["reporter"] == {
        "status": "unhealthy", "ms": body["apps"]["reporter"]["ms"],
        "detail": "store unreachable",
    }


def test_an_app_may_declare_its_own_failure_fatal(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    app = _reporting_site(site, app_package, REPORTING_APP, "reporting_app")
    import reporting_app

    reporting_app.STATE.update(ok=False, fatal=True)
    response = app.test_client().get("/healthz")

    assert response.status_code == 503
    assert response.get_json()["status"] == "unhealthy"


def test_a_raising_check_is_reported_not_propagated(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    """A health check is the last thing that should be able to break a site."""
    app = _reporting_site(site, app_package, BROKEN_REPORTER, "broken_app")
    client = app.test_client()

    health = client.get("/healthz")
    assert health.status_code == 200
    assert "the check itself is broken" in health.get_json()["apps"]["broken"]["detail"]

    status = client.get("/_status")
    assert status.status_code == 200
    assert "so is this one" in status.get_json()["apps"]["broken"]["reported"]["error"]


def test_an_app_that_reports_nothing_is_absent_rather_than_healthy(app: Flask) -> None:
    """The fixture app overrides neither method: silence is not a clean bill."""
    client = app.test_client()
    assert "apps" not in client.get("/healthz").get_json()
    assert "reported" not in client.get("/_status").get_json()["apps"]["widget"]


def test_status_is_not_public(site: SiteFactory) -> None:
    """It reports the database identity, every path, and the build commit.

    404 rather than 403: whether this site is a podpack site at all is not
    something an operator has a reason to publish.
    """
    app = site(admin=None)
    assert app.test_client().get("/_status").status_code == 404


def test_status_answers_an_operator(site: SiteFactory) -> None:
    app = site(admin=lambda: True)
    assert app.test_client().get("/_status").status_code == 200


def test_a_guard_that_raises_denies(site: SiteFactory) -> None:
    """A guard that fails open is not a guard."""
    def broken() -> bool:
        raise RuntimeError("the role table is unreachable")

    app = site(admin=broken)
    assert app.test_client().get("/_status").status_code == 404


def _wire_security(datastore: object) -> Callable[[Flask], None]:
    """A site's `init` doing what flask-security's does: leaving its extension.

    Stubbed rather than installed, because flask-security is a *site's*
    dependency and not podpack's (ADR-0025) -- so these tests must not need it
    present, which is also what proves the check is optional at runtime.
    """
    def _init(app: Flask) -> None:
        app.extensions["security"] = SimpleNamespace(datastore=datastore)

    return _init


def test_a_site_with_no_operator_says_so_at_boot(
    site: SiteFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """404 makes a refusal and a missing route indistinguishable from outside.

    So the reason has to reach the log, or it reaches nobody.
    """
    with caplog.at_level(logging.WARNING, logger="podpack"):
        site(admin=None)
    assert "/_status will answer 404 to everyone" in caplog.text


def test_a_missing_admin_role_says_so_at_boot(
    site: SiteFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """The case that actually happened: predicate wired, role never created.

    The site answered 404 to its own owner, and the only way to find out why
    was to query the database by hand.
    """
    datastore = SimpleNamespace(find_role=lambda name: None)
    with caplog.at_level(logging.WARNING, logger="podpack"):
        site(admin=lambda: True, init=_wire_security(datastore))
    assert "no 'admin' role exists" in caplog.text
    assert "roles create admin" in caplog.text
    assert "roles add <email> admin" in caplog.text


def test_an_existing_admin_role_is_silent(
    site: SiteFactory, caplog: pytest.LogCaptureFixture
) -> None:
    datastore = SimpleNamespace(find_role=lambda name: object())
    with caplog.at_level(logging.WARNING, logger="podpack"):
        site(admin=lambda: True, init=_wire_security(datastore))
    assert "admin" not in caplog.text


def test_a_site_that_wires_no_security_extension_is_silent(
    site: SiteFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """podpack owns no role model, so it has nothing of its own to consult.

    A site whose predicate asks about something else entirely gets no warning:
    a false alarm about a role you deliberately do not use is worse than
    silence.
    """
    with caplog.at_level(logging.WARNING, logger="podpack"):
        site(admin=lambda: True)
    assert "role" not in caplog.text


def test_an_unreadable_role_table_does_not_break_the_boot(
    site: SiteFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Ordinary rather than exceptional: `migrate` creates those tables, and a
    site boots against a database that has none often enough -- in tests, and
    in the window before the first migration runs. A diagnostic that could stop
    a site starting would be a poor trade for the thing it diagnoses."""
    def explode(name: str) -> object:
        raise RuntimeError("relation \"role\" does not exist")

    with caplog.at_level(logging.WARNING, logger="podpack"):
        app = site(admin=lambda: True, init=_wire_security(SimpleNamespace(find_role=explode)))
    assert caplog.text == ""
    assert app.test_client().get("/_status").status_code == 200


def test_healthz_stays_public_but_keeps_an_apps_words_back(
    site: SiteFactory, app_package: Callable[[str, str], str]
) -> None:
    """The container healthcheck needs the status code and nothing else.

    An app's detail names hosts and paths, so the public body says only
    which app is unwell; the sentence is behind the guard.
    """
    app_package("reporting_app", REPORTING_APP)
    config = {"site": {"name": "x", "environment": "test", "apps": ["reporting_app"]}}
    import reporting_app

    reporting_app.STATE.update(ok=False, detail="mongodb://box:27017 unreachable")

    public = site(admin=None, host_config=config).test_client().get("/healthz")
    assert public.status_code == 200
    assert public.get_json()["apps"]["reporter"] == {"status": "unhealthy"}

    operator = site(admin=lambda: True, host_config=config).test_client().get("/healthz")
    assert "mongodb://box:27017" in operator.get_json()["apps"]["reporter"]["detail"]
