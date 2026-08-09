from flask import Blueprint, render_template
from flask.typing import ResponseReturnValue

from podpack import app_config
from podpack.paths import data_dir

blueprint = Blueprint("widget", __name__, template_folder="templates")


@blueprint.route("/")
def index() -> ResponseReturnValue:
    return render_template("widget/index.html", title="Widget")


@blueprint.route("/setting")
def setting() -> ResponseReturnValue:
    return str(app_config().get("size", "unset"))


@blueprint.route("/seeded")
def seeded() -> ResponseReturnValue:
    return (data_dir() / "seed.txt").read_text()
