from __future__ import annotations

import math
from typing import Iterable, List

from backend.domain.run_snapshot import ODDistance
from backend.domain.situation import Situation


class ODDistanceServiceError(ValueError):
    def __init__(self, message: str, *, field: str = "od_distances"):
        super().__init__(message)
        self.field = field


def vincenty_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """WGS-84 inverse Vincenty distance in kilometres.

    This keeps the distance formula used by the 26-4-4 scene generator, but fails
    explicitly if the iteration does not converge instead of silently using the last
    iterate. Coordinates are already canonical domain values; aliases/swapping are not
    accepted here.
    """

    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    lam1 = math.radians(float(lon1))
    lam2 = math.radians(float(lon2))

    a = 6378137.0
    f = 1.0 / 298.257223563
    b = (1.0 - f) * a

    u1 = math.atan((1.0 - f) * math.tan(phi1))
    u2 = math.atan((1.0 - f) * math.tan(phi2))
    L = lam2 - lam1

    sin_u1, cos_u1 = math.sin(u1), math.cos(u1)
    sin_u2, cos_u2 = math.sin(u2), math.cos(u2)

    lamb = L
    converged = False
    sin_sigma = cos_sigma = sigma = sin_alpha = cos2_alpha = 0.0

    for _ in range(1000):
        sin_lamb = math.sin(lamb)
        cos_lamb = math.cos(lamb)
        sin_sigma = math.sqrt(
            (cos_u2 * sin_lamb) ** 2
            + (cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lamb) ** 2
        )
        if sin_sigma == 0.0:
            return 0.0
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lamb
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lamb / sin_sigma
        cos2_alpha = 1.0 - sin_alpha**2
        if cos2_alpha == 0.0:
            C = 0.0
        else:
            C = f / 16.0 * cos2_alpha * (4.0 + f * (4.0 - 3.0 * cos2_alpha))
        previous = lamb
        lamb = L + (1.0 - C) * f * sin_alpha * (
            sigma
            + C
            * sin_sigma
            * (cos2_alpha + C * cos_sigma * (-1.0 + 2.0 * cos2_alpha**2))
        )
        if abs(lamb - previous) < 1e-12:
            converged = True
            break

    if not converged:
        raise ODDistanceServiceError("WGS-84 Vincenty distance did not converge")

    u_sq = cos2_alpha * (a * a - b * b) / (b * b)
    A = 1.0 + u_sq / 16384.0 * (
        4096.0 + u_sq * (-768.0 + u_sq * (320.0 - 175.0 * u_sq))
    )
    B = u_sq / 1024.0 * (
        256.0 + u_sq * (-128.0 + u_sq * (74.0 - 47.0 * u_sq))
    )
    delta_sigma = B * sin_sigma * (
        cos_sigma
        + B
        / 4.0
        * (
            cos_sigma * (-1.0 + 2.0 * cos2_alpha**2)
            - B
            / 6.0
            * cos2_alpha
            * (-3.0 + 4.0 * sin_sigma**2)
            * (-3.0 + 4.0 * cos2_alpha**2)
        )
    )
    metres = b * A * (sigma - delta_sigma)
    kilometres = metres / 1000.0
    if not math.isfinite(kilometres) or kilometres < 0.0:
        raise ODDistanceServiceError("computed OD distance is not finite/nonnegative")
    return kilometres


class ODDistanceService:
    """Build the complete airport × mission distance closure for one Situation."""

    def build_for_situation(self, situation: Situation) -> List[ODDistance]:
        if not isinstance(situation, Situation):
            raise TypeError("situation must be Situation")
        rows: List[ODDistance] = []
        for airport in sorted(situation.airports, key=lambda x: x.airport_id):
            for mission in sorted(situation.missions, key=lambda x: x.mission_id):
                try:
                    distance = vincenty_distance_km(
                        float(airport.airport.latitude),
                        float(airport.airport.longitude),
                        float(mission.latitude),
                        float(mission.longitude),
                    )
                except ODDistanceServiceError as exc:
                    raise ODDistanceServiceError(
                        f"OD distance failed for airport={airport.airport_id}, mission={mission.mission_id}: {exc}",
                        field=f"od_distances.{airport.airport_id}.{mission.mission_id}",
                    ) from exc
                rows.append(
                    ODDistance(
                        airport_id=airport.airport_id,
                        mission_id=mission.mission_id,
                        distance_km=distance,
                    )
                )
        return rows


__all__ = [
    "ODDistanceService",
    "ODDistanceServiceError",
    "vincenty_distance_km",
]
