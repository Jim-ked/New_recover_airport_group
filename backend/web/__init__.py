"""Thin HTTP/API adapters.

No route in this package may read algorithm internals or persistence directly. Web binds
validated HTTP input to application services and serializes explicit responses only.
"""

from .run_api import RunApi
from .results_api import ResultsApi

__all__ = ["RunApi", "ResultsApi"]
