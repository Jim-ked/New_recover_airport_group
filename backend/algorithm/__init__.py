"""Immutable algorithm execution boundary for the recovered airport-group system."""

from .snapshot_adapter import AlgorithmInputBundle, AlgorithmInputError, build_algorithm_input
from .runner import AlgorithmInfeasibleError, AlgorithmRunError, AlgorithmRunResult, run_once

__all__ = [
    "AlgorithmInputBundle",
    "AlgorithmInputError",
    "build_algorithm_input",
    "AlgorithmInfeasibleError",
    "AlgorithmRunError",
    "AlgorithmRunResult",
    "run_once",
]
