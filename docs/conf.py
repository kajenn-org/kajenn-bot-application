# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Sphinx configuration for the bot application package."""

from importlib.metadata import version as package_version

project = "kajenn-bot-application"
author = "Genropy Team"
copyright = "2026, Softwell S.r.l."
release = package_version("kajenn-bot-application")
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "myst_parser"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
myst_heading_anchors = 3
html_theme = "sphinx_rtd_theme"
exclude_patterns = ["_build"]
autodoc_member_order = "bysource"
autodoc_typehints = "description"

version = release

language = "en"
html_title = "kajenn-bot-application"
html_short_title = "Bot applications"
html_baseurl = "https://kajenn-bot-application.readthedocs.io/en/latest/"
html_theme_options = {"navigation_depth": 3, "collapse_navigation": False}
html_static_path = ["_static"]
html_css_files = ["readability.css"]
html_context = {
    "display_github": True,
    "github_user": "kajenn-org",
    "github_repo": "kajenn-bot-application",
    "github_version": "main",
    "conf_py_path": "/docs/",
}

# Resolve every local cross-reference. These external annotation classes have no
# target in this documentation; preserve their visible type names in signatures.
nitpicky = True
nitpick_ignore = [
    ("py:class", "httpx.AsyncClient"),
    ("py:class", "genro_routes.core.routing.RoutingClass"),
    ("py:class", "datetime.datetime"),
]
