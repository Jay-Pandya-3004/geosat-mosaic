# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# -- Path setup ----------------------------------------------------------------
# Make the `global_mosaic` package and the standalone `scripts/` modules
# importable by autodoc without installing the package.
sys.path.insert(0, os.path.abspath('../../src'))
sys.path.insert(0, os.path.abspath('../../scripts'))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'Global Mosaic'
copyright = '2026, Global Mosaic project'
author = 'Global Mosaic project'

version = '0.1.0'
release = '0.1.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
]

templates_path = ['_templates']
exclude_patterns = []

language = 'en'

# -- Autodoc / Napoleon -------------------------------------------------------
# Google-style docstrings (Args/Returns/Raises) are used throughout the
# global_mosaic package and the scripts/ directory -- napoleon converts them
# into proper Sphinx field lists for autodoc.
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_rtype = False

autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': True,
}
# Keep the source order modules define things in, rather than alphabetizing.
autodoc_member_order = 'bysource'
# Render type hints in the description instead of cluttering signatures.
autodoc_typehints = 'description'

# The package pulls in a number of heavy scientific/geospatial and
# network-facing third-party dependencies. Installing the full stack
# (satpy, cartopy, dask, etc.) is impractical just to build docs, so autodoc
# mocks them out: it never actually imports them, it just returns a stand-in
# object for any attribute access, which is enough for signature/docstring
# introspection. Lightweight, easily-installed deps (requests, numpy, pyyaml,
# python-dotenv, Pillow) are installed for real instead of mocked -- see
# requirements-docs.txt.
autodoc_mock_imports = [
    'satpy',
    'pyresample',
    'xarray',
    'dask',
    'zarr',
    's3fs',
    'boto3',
    'eumdac',
    'cartopy',
    'matplotlib',
    'netCDF4',
    'h5py',
    'openpyxl',
    'pyorbital',
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'furo'
html_static_path = ['_static']
