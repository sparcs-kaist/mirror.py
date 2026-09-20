import os
import sys
from datetime import datetime
from pathlib import Path

from sphinx.errors import SphinxError

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
exclude_patterns = ["editor/**", "_build/**"]

# -- HTML output (Read the Docs theme) --
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "titles_only": False,
}
html_title = f"mirror.py {release}"
templates_path = ["_templates"]


def check_editor_assets(app) -> None:
    """Require the configuration editor bundle before an HTML build.

    Args:
        app: The Sphinx application invoking the builder.
    """
    if app.builder.format != "html":
        return
    assets = Path(app.confdir) / "_static" / "config-editor"
    if not all((assets / name).is_file() for name in ("editor.js", "editor.css")):
        raise SphinxError(
            "Configuration editor assets are missing. Run "
            "'npm --prefix docs/editor run docs:build' from the repository root."
        )


def select_editor_template(app, pagename, templatename, context, doctree):
    """Select the editor-only template without loading assets on other pages.

    Args:
        app: The Sphinx application.
        pagename: Name of the page being rendered.
        templatename: Default template name.
        context: Template variables.
        doctree: Page document tree.

    Returns:
        The editor template name, or None for other pages.
    """
    if pagename == "guide/config-editor":
        return "config-editor.html"
    return None


def setup(app) -> dict:
    """Register the editor asset check and page template.

    Args:
        app: The Sphinx application.

    Returns:
        Parallel build compatibility metadata.
    """
    app.connect("builder-inited", check_editor_assets)
    app.connect("html-page-context", select_editor_template)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
