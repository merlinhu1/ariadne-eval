from __future__ import annotations

import shutil
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path


PLUGIN_NAME = "ariadne-eval"


def _copytree_from_resource(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
            continue
        target = destination / child.name
        if child.is_dir():
            _copytree_from_resource(child, target)
        else:
            with child.open("rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def install_dashboard_plugin(hermes_home: str | Path) -> Path:
    """Install the bundled Hermes dashboard plugin into a Hermes home.

    Hermes discovers user dashboard plugins from ``$HERMES_HOME/plugins``. This
    copies Ariadne Eval's read-only dashboard tab there so the existing Hermes
    dashboard can mount it without requiring a core Hermes patch.
    """
    home = Path(hermes_home).expanduser()
    destination = home / "plugins" / PLUGIN_NAME / "dashboard"
    source = resources.files(__package__) / "dashboard"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    _copytree_from_resource(source, destination)
    return destination
