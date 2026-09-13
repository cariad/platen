from pathlib import Path

from ruamel.yaml import YAML

from platen import Platen

here = Path(__file__).parent
values = YAML(typ="safe").load(here / "tasks.yaml")

platen = Platen(
    here / "templates",
    here / "build",
    values,
)

platen.press("tasks.html")
platen.press("tasks.md")
