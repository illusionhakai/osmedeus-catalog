"""Public application API for Bug Bounty Catalog."""

from .application import CatalogService
from .query import AssetQuery, Page, ProgramQuery
from .sources import available_sources, register_source

__all__ = [
    "AssetQuery",
    "CatalogService",
    "Page",
    "ProgramQuery",
    "available_sources",
    "register_source",
    "__version__",
]

__version__ = "0.3.0"
