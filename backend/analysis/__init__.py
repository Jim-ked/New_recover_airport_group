"""Backend-derived analysis facts for successful canonical Runs."""

from .metrics import MetricsBuildError, build_metrics_core
from .comparison import (
    ComparisonError,
    build_configuration_comparison,
    build_multi_scenario_comparison,
    build_r0_r1_r2_comparison,
    check_configuration_comparable,
    check_multi_scenario_comparable,
    check_r0_r1_r2,
)

__all__ = [
    "MetricsBuildError",
    "build_metrics_core",
    "ComparisonError",
    "build_r0_r1_r2_comparison",
    "build_multi_scenario_comparison",
    "build_configuration_comparison",
    "check_configuration_comparable",
    "check_multi_scenario_comparable",
    "check_r0_r1_r2",
]
