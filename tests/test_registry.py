"""What the registry promises, tested rather than assumed.

Each of these corresponds to something a Django app gets for free and Flask does
not, so a failure here means the plugin mechanism has stopped being a plugin
mechanism.
"""

import pytest

from podpack import Section, SiteApp, create_app, db


def test_app_list_is_configuration_not_code(site):
    """Installing an app must be an edit to the config file and nothing else."""
    with_notes = site()
    without = site(host_config={"site": {"name": "bare", "environment": "test", "apps": []}})

    assert "notes.index" in with_notes.view_functions
    assert "notes.index" not in without.view_functions
    assert without.extensions["podpack"].nav == []


def test_models_reach_metadata(app):
    """The claim alembic depends on.

    Autogenerate reads `db.metadata` after building an app, so an installed
    app's tables have to be registered by the act of installing it. If this
    fails, migrations silently stop seeing the app.
    """
    assert "notes" in db.metadata.tables


def test_nav_is_contributed_by_apps(app, client):
    assert app.extensions["podpack"].nav == [Section("Notes", "/notes/")]
    body = client.get("/").get_data(as_text=True)
    assert 'href="/notes/"' in body


def test_app_template_is_namespaced_and_used(client):
    """`notes/index.html` must resolve to the app's own copy."""
    response = client.get("/notes/")
    assert response.status_code == 200
    assert "<h2>Notes</h2>" in response.get_data(as_text=True)


def test_app_renders_on_a_site_with_no_chrome(site, site_package):
    """An app must render against a site that ships no base.html of its own.

    This is the one that actually exercises the fallback loader: the site
    package below has a template directory but no `base.html`, and the notes
    template extends one. Without podpack's loader appended *after* the
    blueprints, this raises TemplateNotFound.
    """
    site_package("bare_site", {})
    app = site(site_package="bare_site")
    body = app.test_client().get("/notes/").get_data(as_text=True)

    assert "<h2>Notes</h2>" in body
    assert "Served by podpack" in body  # podpack's default chrome


def test_site_can_override_an_app_template(site, site_package):
    """A site overrides an app's template by shipping the same namespaced path.

    This precedence is Flask's own ordering rather than anything podpack adds,
    but the whole template story rests on it, so it is worth a test that would
    catch the loader wiring breaking it.
    """
    site_package("mysite", {"notes/index.html": "OVERRIDDEN"})
    app = site(site_package="mysite")

    assert app.test_client().get("/notes/").get_data(as_text=True) == "OVERRIDDEN"


def test_per_app_directories_are_created(app):
    state = app.extensions["podpack"]
    assert (state.data_root / "notes").is_dir()
    assert (state.log_root / "notes").is_dir()


def test_shipped_data_is_seeded_once(app, site):
    """Seeding follows the db-init rule: first time on this machine, not every
    restart. Re-arming means deleting the app's host data directory."""
    welcome = app.extensions["podpack"].data_root / "notes" / "welcome.md"
    assert welcome.is_file()

    welcome.write_text("edited on the host")
    site()  # a restart: same roots, app installed again
    assert welcome.read_text() == "edited on the host"

    welcome.unlink()
    site()  # still not empty? it is now, so seeding re-arms
    assert "ships inside the notes app" in welcome.read_text()


def test_seeded_data_is_read_from_the_host_copy(app, client):
    """Editing the host copy must change the page, with no rebuild."""
    welcome = app.extensions["podpack"].data_root / "notes" / "welcome.md"
    welcome.write_text("host-side edit")
    assert "host-side edit" in client.get("/notes/").get_data(as_text=True)


def test_app_config_is_namespaced(client, app):
    """An app reads its own section of the host config and no one else's."""
    from podpack import app_config

    with app.test_request_context("/notes/"):
        assert app_config().get("page_size") == 5
        assert app_config("notes") == {"page_size": 5}


def test_migration_metadata_needs_no_application(tmp_path, monkeypatch):
    """The migration environment must not need a Flask app, or its secrets.

    Building the target metadata deliberately does not call `create_app`, so
    that a broken factory is not also a broken migration. Deleting both
    variables `create_app` requires is what proves the independence: if this
    ever starts building an app, it will fail here rather than in production.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)

    config = tmp_path / "app.toml"
    config.write_text('[site]\nname = "x"\napps = ["podpack_notes"]\n')

    from podpack.migrations import target_metadata

    assert "notes" in target_metadata(config).tables


def test_unknown_app_is_a_boot_failure(site):
    with pytest.raises(ModuleNotFoundError):
        site(host_config={"site": {"name": "x", "environment": "test", "apps": ["no_such_app"]}})


def test_module_without_site_app_is_rejected(site):
    """A clear error beats a site that boots with a feature silently missing."""
    with pytest.raises(RuntimeError, match="no module-level"):
        site(host_config={"site": {"name": "x", "environment": "test", "apps": ["json"]}})
