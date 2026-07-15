import os
import sys
from datetime import datetime

# conf.py lives in docs/, the package is one level up.
sys.path.insert(0, os.path.abspath(".."))

import mirror  # noqa: E402 -- verified import-safe (no FS/socket/subprocess side effects)

# -- Project information --
project = "mirror.py"
author = "SPARCS@KAIST"
copyright = f"{datetime.now():%Y}, {author}"
release = mirror.__version__
version = ".".join(release.split(".")[:2])

# -- General configuration --
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_member_order = "bysource"
autodoc_mock_imports = ["bandersnatch"]

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = False
napoleon_use_rtype = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "click": ("https://click.palletsprojects.com/en/stable/", None),
}

myst_enable_extensions = ["colon_fence", "deflist", "substitution"]
myst_heading_anchors = 3

# Placeholder JSON blocks in the included config.md (e.g. {"settings": { ... }}) are not
# valid JSON, so Pygments cannot lex them and emits misc.highlighting_failure. The blocks
# still render in relaxed mode; suppress only that warning category so a strict -W build
# does not fail on cosmetic highlighting.
suppress_warnings = ["misc.highlighting_failure"]

source_suffix = {".md": "markdown", ".rst": "restructuredtext"}

# -- HTML output (Read the Docs theme) --
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "titles_only": False,
}
html_title = f"mirror.py {release}"
