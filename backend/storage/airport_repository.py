from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend.domain.airport import AirportBase, RunwayBase, RunwayEnd
from backend.domain.airport_operations import (
    AirportAircraftSupport,
    AirportOperationalProfile,
    AirportResourceStock,
)
from backend.domain.catalog import (
    AircraftResourceRequirement,
    AircraftType,
    ResourceType,
)


from backend.storage.database import initialize_database


class CatalogConflictError(ValueError):
    pass


class CatalogReferenceError(ValueError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class AirportRepository:
    """SQLite authority for static airport data, catalogs and reusable baseline profiles."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    # ---------- static airports ----------

    def save_airport(self, airport: AirportBase) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO airports (
                    airport_id, airport_name, facility_type, role, icao_code, iata_code,
                    region, municipality, longitude, latitude, elevation_m,
                    scheduled_service, runways_known
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(airport_id) DO UPDATE SET
                    airport_name=excluded.airport_name,
                    facility_type=excluded.facility_type,
                    role=excluded.role,
                    icao_code=excluded.icao_code,
                    iata_code=excluded.iata_code,
                    region=excluded.region,
                    municipality=excluded.municipality,
                    longitude=excluded.longitude,
                    latitude=excluded.latitude,
                    elevation_m=excluded.elevation_m,
                    scheduled_service=excluded.scheduled_service,
                    runways_known=excluded.runways_known
                """,
                (
                    airport.airport_id,
                    airport.airport_name,
                    airport.facility_type,
                    airport.role,
                    airport.icao_code,
                    airport.iata_code,
                    airport.region,
                    airport.municipality,
                    float(airport.longitude),
                    float(airport.latitude),
                    None if airport.elevation_m is None else float(airport.elevation_m),
                    int(airport.scheduled_service),
                    int(airport.runways is not None),
                ),
            )
            conn.execute("DELETE FROM runways WHERE airport_id = ?", (airport.airport_id,))
            if airport.runways is not None:
                for runway in airport.runways:
                    self._insert_runway(conn, airport.airport_id, runway)

    def save_airports(self, airports: Iterable[AirportBase]) -> None:
        with self.connect() as conn:
            for airport in airports:
                conn.execute(
                    """
                    INSERT INTO airports (
                        airport_id, airport_name, facility_type, role, icao_code, iata_code,
                        region, municipality, longitude, latitude, elevation_m,
                        scheduled_service, runways_known
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(airport_id) DO UPDATE SET
                        airport_name=excluded.airport_name,
                        facility_type=excluded.facility_type,
                        role=excluded.role,
                        icao_code=excluded.icao_code,
                        iata_code=excluded.iata_code,
                        region=excluded.region,
                        municipality=excluded.municipality,
                        longitude=excluded.longitude,
                        latitude=excluded.latitude,
                        elevation_m=excluded.elevation_m,
                        scheduled_service=excluded.scheduled_service,
                        runways_known=excluded.runways_known
                    """,
                    (
                        airport.airport_id,
                        airport.airport_name,
                        airport.facility_type,
                        airport.role,
                        airport.icao_code,
                        airport.iata_code,
                        airport.region,
                        airport.municipality,
                        float(airport.longitude),
                        float(airport.latitude),
                        None if airport.elevation_m is None else float(airport.elevation_m),
                        int(airport.scheduled_service),
                        int(airport.runways is not None),
                    ),
                )
                conn.execute("DELETE FROM runways WHERE airport_id = ?", (airport.airport_id,))
                if airport.runways is not None:
                    for runway in airport.runways:
                        self._insert_runway(conn, airport.airport_id, runway)

    def _insert_runway(self, conn: sqlite3.Connection, airport_id: str, runway: RunwayBase) -> None:
        lo = runway.low_end
        hi = runway.high_end
        conn.execute(
            """
            INSERT INTO runways (
                runway_id, airport_id, length_m, width_m, surface, lighted,
                low_ident, low_latitude, low_longitude, low_elevation_m,
                low_heading_deg_true, low_displaced_threshold_m,
                high_ident, high_latitude, high_longitude, high_elevation_m,
                high_heading_deg_true, high_displaced_threshold_m
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runway.runway_id,
                airport_id,
                None if runway.length_m is None else float(runway.length_m),
                None if runway.width_m is None else float(runway.width_m),
                runway.surface,
                None if runway.lighted is None else int(runway.lighted),
                None if lo is None else lo.ident,
                None if lo is None or lo.latitude is None else float(lo.latitude),
                None if lo is None or lo.longitude is None else float(lo.longitude),
                None if lo is None or lo.elevation_m is None else float(lo.elevation_m),
                None if lo is None or lo.heading_deg_true is None else float(lo.heading_deg_true),
                None if lo is None or lo.displaced_threshold_m is None else float(lo.displaced_threshold_m),
                None if hi is None else hi.ident,
                None if hi is None or hi.latitude is None else float(hi.latitude),
                None if hi is None or hi.longitude is None else float(hi.longitude),
                None if hi is None or hi.elevation_m is None else float(hi.elevation_m),
                None if hi is None or hi.heading_deg_true is None else float(hi.heading_deg_true),
                None if hi is None or hi.displaced_threshold_m is None else float(hi.displaced_threshold_m),
            ),
        )

    def get_airport(self, airport_id: str) -> AirportBase:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM airports WHERE airport_id = ?", (airport_id,)).fetchone()
            if row is None:
                raise KeyError(f"airport not found: {airport_id}")
            runway_rows = conn.execute(
                "SELECT * FROM runways WHERE airport_id = ? ORDER BY runway_id", (airport_id,)
            ).fetchall()
        return self._airport_from_rows(row, runway_rows)

    def list_airports(self) -> List[AirportBase]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM airports ORDER BY airport_id").fetchall()
            runway_rows = conn.execute("SELECT * FROM runways ORDER BY airport_id, runway_id").fetchall()
        by_airport = {}
        for row in runway_rows:
            by_airport.setdefault(row["airport_id"], []).append(row)
        return [self._airport_from_rows(row, by_airport.get(row["airport_id"], [])) for row in rows]

    def count_airports(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM airports").fetchone()
        return int(row["c"])

    @staticmethod
    def _end_from_row(row: sqlite3.Row, prefix: str) -> Optional[RunwayEnd]:
        values = (
            row[f"{prefix}_ident"],
            row[f"{prefix}_latitude"],
            row[f"{prefix}_longitude"],
            row[f"{prefix}_elevation_m"],
            row[f"{prefix}_heading_deg_true"],
            row[f"{prefix}_displaced_threshold_m"],
        )
        if all(v is None for v in values):
            return None
        return RunwayEnd(
            ident=row[f"{prefix}_ident"],
            latitude=row[f"{prefix}_latitude"],
            longitude=row[f"{prefix}_longitude"],
            elevation_m=row[f"{prefix}_elevation_m"],
            heading_deg_true=row[f"{prefix}_heading_deg_true"],
            displaced_threshold_m=row[f"{prefix}_displaced_threshold_m"],
        )

    def _airport_from_rows(self, row: sqlite3.Row, runway_rows: Sequence[sqlite3.Row]) -> AirportBase:
        runways = None
        if bool(row["runways_known"]):
            runways = tuple(
                RunwayBase(
                    runway_id=r["runway_id"],
                    length_m=r["length_m"],
                    width_m=r["width_m"],
                    surface=r["surface"],
                    lighted=None if r["lighted"] is None else bool(r["lighted"]),
                    low_end=self._end_from_row(r, "low"),
                    high_end=self._end_from_row(r, "high"),
                )
                for r in runway_rows
            )
        return AirportBase(
            airport_id=row["airport_id"],
            airport_name=row["airport_name"],
            facility_type=row["facility_type"],
            role=row["role"],
            longitude=row["longitude"],
            latitude=row["latitude"],
            scheduled_service=bool(row["scheduled_service"]),
            icao_code=row["icao_code"],
            iata_code=row["iata_code"],
            region=row["region"],
            municipality=row["municipality"],
            elevation_m=row["elevation_m"],
            runways=runways,
        )

    # ---------- catalogs ----------

    def save_aircraft_type(self, item: AircraftType) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO aircraft_types (
                    aircraft_type_id, name, speed_kmh, max_range_km, reserve_ratio,
                    departure_capacity_occupancy_factor, arrival_capacity_occupancy_factor
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(aircraft_type_id) DO UPDATE SET
                    name=excluded.name,
                    speed_kmh=excluded.speed_kmh,
                    max_range_km=excluded.max_range_km,
                    reserve_ratio=excluded.reserve_ratio,
                    departure_capacity_occupancy_factor=excluded.departure_capacity_occupancy_factor,
                    arrival_capacity_occupancy_factor=excluded.arrival_capacity_occupancy_factor
                """,
                (
                    item.aircraft_type_id,
                    item.name,
                    item.speed_kmh,
                    item.max_range_km,
                    item.reserve_ratio,
                    item.departure_capacity_occupancy_factor,
                    item.arrival_capacity_occupancy_factor,
                ),
            )

    def save_resource_type(self, item: ResourceType) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO resource_types (resource_type_id, name, category, unit)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(resource_type_id) DO UPDATE SET
                    name=excluded.name, category=excluded.category, unit=excluded.unit
                """,
                (item.resource_type_id, item.name, item.category, item.unit),
            )

    def save_aircraft_resource_requirement(self, item: AircraftResourceRequirement) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO aircraft_resource_requirements (
                    aircraft_type_id, resource_type_id, basis, quantity
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(aircraft_type_id, resource_type_id, basis) DO UPDATE SET
                    quantity=excluded.quantity
                """,
                (item.aircraft_type_id, item.resource_type_id, item.basis, item.quantity),
            )

    def get_aircraft_type(self, aircraft_type_id: str) -> AircraftType:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM aircraft_types WHERE aircraft_type_id = ?",
                (aircraft_type_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"aircraft type not found: {aircraft_type_id}")
        return AircraftType(
            aircraft_type_id=row["aircraft_type_id"],
            name=row["name"],
            speed_kmh=row["speed_kmh"],
            max_range_km=row["max_range_km"],
            reserve_ratio=row["reserve_ratio"],
            departure_capacity_occupancy_factor=row["departure_capacity_occupancy_factor"],
            arrival_capacity_occupancy_factor=row["arrival_capacity_occupancy_factor"],
        )

    def list_aircraft_types(self) -> List[AircraftType]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM aircraft_types ORDER BY aircraft_type_id").fetchall()
        return [
            AircraftType(
                aircraft_type_id=row["aircraft_type_id"],
                name=row["name"],
                speed_kmh=row["speed_kmh"],
                max_range_km=row["max_range_km"],
                reserve_ratio=row["reserve_ratio"],
                departure_capacity_occupancy_factor=row["departure_capacity_occupancy_factor"],
                arrival_capacity_occupancy_factor=row["arrival_capacity_occupancy_factor"],
                )
            for row in rows
        ]

    def list_resource_types(self) -> List[ResourceType]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM resource_types ORDER BY resource_type_id").fetchall()
        return [
            ResourceType(
                resource_type_id=row["resource_type_id"],
                name=row["name"],
                category=row["category"],
                unit=row["unit"],
            )
            for row in rows
        ]

    def list_aircraft_resource_requirements(self) -> List[AircraftResourceRequirement]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT aircraft_type_id, resource_type_id, basis, quantity
                FROM aircraft_resource_requirements
                ORDER BY aircraft_type_id, resource_type_id, basis
                """
            ).fetchall()
        return [
            AircraftResourceRequirement(
                aircraft_type_id=row["aircraft_type_id"],
                resource_type_id=row["resource_type_id"],
                basis=row["basis"],
                quantity=row["quantity"],
            )
            for row in rows
        ]

    # ---------- operational baseline ----------

    def save_operational_profile(self, profile: AirportOperationalProfile) -> None:
        # Profile object already enforces completeness semantics. FK checks catalog references.
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO airport_operational_profiles (
                    airport_id, configuration_complete, capacity_per_window, support_level
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(airport_id) DO UPDATE SET
                    configuration_complete=excluded.configuration_complete,
                    capacity_per_window=excluded.capacity_per_window,
                    support_level=excluded.support_level
                """,
                (
                    profile.airport_id,
                    int(profile.configuration_complete),
                    profile.capacity_per_window,
                    profile.support_level,
                ),
            )
            conn.execute("DELETE FROM airport_aircraft_support WHERE airport_id = ?", (profile.airport_id,))
            conn.execute("DELETE FROM airport_resource_stocks WHERE airport_id = ?", (profile.airport_id,))
            for row in profile.aircraft_support:
                conn.execute(
                    """
                    INSERT INTO airport_aircraft_support (
                        airport_id, aircraft_type_id, initial_quantity, tau_reset_windows
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        profile.airport_id,
                        row.aircraft_type_id,
                        row.initial_quantity,
                        row.tau_reset_windows,
                    ),
                )
            for row in profile.resource_stocks:
                conn.execute(
                    """
                    INSERT INTO airport_resource_stocks (
                        airport_id, resource_type_id, quantity, replenishment_capacity_per_window
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        profile.airport_id,
                        row.resource_type_id,
                        row.initial_quantity,
                        row.replenishment_capacity_per_window,
                    ),
                )

    def get_operational_profile(self, airport_id: str) -> AirportOperationalProfile:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM airport_operational_profiles WHERE airport_id = ?", (airport_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"airport operational profile not found: {airport_id}")
            support_rows = conn.execute(
                """
                SELECT aircraft_type_id, initial_quantity, tau_reset_windows
                FROM airport_aircraft_support
                WHERE airport_id = ? ORDER BY aircraft_type_id
                """,
                (airport_id,),
            ).fetchall()
            stock_rows = conn.execute(
                """
                SELECT resource_type_id, quantity, replenishment_capacity_per_window
                FROM airport_resource_stocks
                WHERE airport_id = ? ORDER BY resource_type_id
                """,
                (airport_id,),
            ).fetchall()
        return AirportOperationalProfile(
            airport_id=row["airport_id"],
            configuration_complete=bool(row["configuration_complete"]),
            capacity_per_window=row["capacity_per_window"],
            support_level=row["support_level"],
            aircraft_support=tuple(
                AirportAircraftSupport(
                    aircraft_type_id=r["aircraft_type_id"],
                    initial_quantity=r["initial_quantity"],
                    tau_reset_windows=r["tau_reset_windows"],
                )
                for r in support_rows
            ),
            resource_stocks=tuple(
                AirportResourceStock(
                    resource_type_id=r["resource_type_id"],
                    initial_quantity=r["quantity"],
                    replenishment_capacity_per_window=r["replenishment_capacity_per_window"],
                )
                for r in stock_rows
            ),
        )

    # ---------- frontend catalog/BFF helpers ----------

    @staticmethod
    def _airport_summary_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "airport_id": row["airport_id"],
            "airport_name": row["airport_name"],
            "facility_type": row["facility_type"],
            "role": row["role"],
            "region": row["region"],
            "municipality": row["municipality"],
            "longitude": row["longitude"],
            "latitude": row["latitude"],
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "configuration_complete": None if row["configuration_complete"] is None else bool(row["configuration_complete"]),
            "capacity_per_window": row["capacity_per_window"],
            "support_level": row["support_level"],
            "supported_aircraft_type_count": int(row["supported_aircraft_type_count"] or 0),
        }

    def get_airport_metadata(self, airport_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.airport_id, a.airport_name, a.facility_type, a.role, a.region, a.municipality,
                       a.longitude, a.latitude, a.revision, a.created_at, a.updated_at,
                       p.configuration_complete, p.capacity_per_window, p.support_level,
                       (SELECT COUNT(*) FROM airport_aircraft_support s WHERE s.airport_id=a.airport_id)
                         AS supported_aircraft_type_count
                FROM airports a
                LEFT JOIN airport_operational_profiles p ON p.airport_id=a.airport_id
                WHERE a.airport_id=?
                """,
                (airport_id,),
            ).fetchone()
        return None if row is None else self._airport_summary_row(row)

    def get_airport_bundle(self, airport_id: str) -> Dict[str, Any]:
        airport = self.get_airport(airport_id)
        try:
            profile = self.get_operational_profile(airport_id)
        except KeyError:
            profile = None
        meta = self.get_airport_metadata(airport_id)
        if meta is None:
            raise KeyError(f"airport not found: {airport_id}")
        return {
            "airport": airport.to_dict(),
            "operational_profile": None if profile is None else profile.to_dict(),
            "metadata": meta,
        }

    def list_airport_bundles(
        self,
        *,
        query: Optional[str] = None,
        roles: Optional[Sequence[str]] = None,
        regions: Optional[Sequence[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
            raise ValueError("limit must be in [1,500]")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be nonnegative")
        clauses: List[str] = []
        params: List[Any] = []
        if query is not None and query.strip():
            q = f"%{query.strip()}%"
            clauses.append("(a.airport_id LIKE ? OR a.airport_name LIKE ? OR COALESCE(a.icao_code,'') LIKE ? OR COALESCE(a.iata_code,'') LIKE ?)")
            params.extend([q, q, q, q])
        if roles:
            vals = tuple(str(x) for x in roles)
            placeholders = ",".join("?" for _ in vals)
            clauses.append(f"a.role IN ({placeholders})")
            params.extend(vals)
        if regions:
            vals = tuple(str(x) for x in regions)
            placeholders = ",".join("?" for _ in vals)
            clauses.append(f"a.region IN ({placeholders})")
            params.extend(vals)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        base_sql = f"""
            FROM airports a
            LEFT JOIN airport_operational_profiles p ON p.airport_id=a.airport_id
            {where}
        """
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS c {base_sql}", tuple(params)).fetchone()["c"])
            rows = conn.execute(
                f"""
                SELECT a.airport_id, a.airport_name, a.facility_type, a.role, a.region, a.municipality,
                       a.longitude, a.latitude, a.revision, a.created_at, a.updated_at,
                       p.configuration_complete, p.capacity_per_window, p.support_level,
                       (SELECT COUNT(*) FROM airport_aircraft_support s WHERE s.airport_id=a.airport_id)
                         AS supported_aircraft_type_count
                {base_sql}
                ORDER BY COALESCE(a.updated_at,a.created_at) DESC, a.airport_id
                LIMIT ? OFFSET ?
                """,
                tuple(params + [limit, offset]),
            ).fetchall()
        return [self._airport_summary_row(row) for row in rows], total

    @staticmethod
    def _write_airport(conn: sqlite3.Connection, airport: AirportBase, *, is_create: bool) -> None:
        if is_create:
            conn.execute(
                """
                INSERT INTO airports (
                    airport_id, airport_name, facility_type, role, icao_code, iata_code,
                    region, municipality, longitude, latitude, elevation_m,
                    scheduled_service, runways_known, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    airport.airport_id, airport.airport_name, airport.facility_type, airport.role,
                    airport.icao_code, airport.iata_code, airport.region, airport.municipality,
                    float(airport.longitude), float(airport.latitude),
                    None if airport.elevation_m is None else float(airport.elevation_m),
                    int(airport.scheduled_service), int(airport.runways is not None),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE airports SET
                    airport_name=?, facility_type=?, role=?, icao_code=?, iata_code=?, region=?, municipality=?,
                    longitude=?, latitude=?, elevation_m=?, scheduled_service=?, runways_known=?,
                    revision=revision+1, updated_at=CURRENT_TIMESTAMP
                WHERE airport_id=?
                """,
                (
                    airport.airport_name, airport.facility_type, airport.role, airport.icao_code, airport.iata_code,
                    airport.region, airport.municipality, float(airport.longitude), float(airport.latitude),
                    None if airport.elevation_m is None else float(airport.elevation_m), int(airport.scheduled_service),
                    int(airport.runways is not None), airport.airport_id,
                ),
            )
        conn.execute("DELETE FROM runways WHERE airport_id=?", (airport.airport_id,))
        if airport.runways is not None:
            for runway in airport.runways:
                # Duplicated here intentionally so the entire bundle remains one transaction.
                lo, hi = runway.low_end, runway.high_end
                conn.execute(
                    """
                    INSERT INTO runways (
                        runway_id, airport_id, length_m, width_m, surface, lighted,
                        low_ident, low_latitude, low_longitude, low_elevation_m, low_heading_deg_true, low_displaced_threshold_m,
                        high_ident, high_latitude, high_longitude, high_elevation_m, high_heading_deg_true, high_displaced_threshold_m
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        runway.runway_id, airport.airport_id, runway.length_m, runway.width_m, runway.surface,
                        None if runway.lighted is None else int(runway.lighted),
                        None if lo is None else lo.ident, None if lo is None else lo.latitude,
                        None if lo is None else lo.longitude, None if lo is None else lo.elevation_m,
                        None if lo is None else lo.heading_deg_true, None if lo is None else lo.displaced_threshold_m,
                        None if hi is None else hi.ident, None if hi is None else hi.latitude,
                        None if hi is None else hi.longitude, None if hi is None else hi.elevation_m,
                        None if hi is None else hi.heading_deg_true, None if hi is None else hi.displaced_threshold_m,
                    ),
                )

    @staticmethod
    def _write_profile(conn: sqlite3.Connection, profile: AirportOperationalProfile) -> None:
        conn.execute(
            """
            INSERT INTO airport_operational_profiles (airport_id, configuration_complete, capacity_per_window, support_level)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(airport_id) DO UPDATE SET
                configuration_complete=excluded.configuration_complete,
                capacity_per_window=excluded.capacity_per_window,
                support_level=excluded.support_level
            """,
            (profile.airport_id, int(profile.configuration_complete), profile.capacity_per_window, profile.support_level),
        )
        conn.execute("DELETE FROM airport_aircraft_support WHERE airport_id=?", (profile.airport_id,))
        conn.execute("DELETE FROM airport_resource_stocks WHERE airport_id=?", (profile.airport_id,))
        for row in profile.aircraft_support:
            conn.execute(
                "INSERT INTO airport_aircraft_support (airport_id, aircraft_type_id, initial_quantity, tau_reset_windows) VALUES (?, ?, ?, ?)",
                (profile.airport_id, row.aircraft_type_id, row.initial_quantity, row.tau_reset_windows),
            )
        for row in profile.resource_stocks:
            conn.execute(
                "INSERT INTO airport_resource_stocks (airport_id, resource_type_id, quantity, replenishment_capacity_per_window) VALUES (?, ?, ?, ?)",
                (profile.airport_id, row.resource_type_id, row.initial_quantity, row.replenishment_capacity_per_window),
            )

    def save_airport_bundle(
        self,
        *,
        airport: AirportBase,
        operational_profile: Optional[AirportOperationalProfile],
        create_only: bool = False,
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        if operational_profile is not None and operational_profile.airport_id != airport.airport_id:
            raise ValueError("airport and operational_profile airport_id must match")
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM airports WHERE airport_id=?", (airport.airport_id,)).fetchone()
            exists = row is not None
            if create_only and exists:
                raise CatalogConflictError(f"airport already exists: {airport.airport_id}")
            if not create_only and not exists:
                raise KeyError(f"airport not found: {airport.airport_id}")
            if exists and expected_revision is not None and int(row["revision"]) != int(expected_revision):
                raise CatalogConflictError(
                    f"airport revision conflict: expected {expected_revision}, current {row['revision']}"
                )
            self._write_airport(conn, airport, is_create=not exists)
            if operational_profile is not None:
                self._write_profile(conn, operational_profile)
        return self.get_airport_bundle(airport.airport_id)

    def delete_airport(self, airport_id: str, *, expected_revision: Optional[int] = None) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM airports WHERE airport_id=?", (airport_id,)).fetchone()
            if row is None:
                raise KeyError(f"airport not found: {airport_id}")
            if expected_revision is not None and int(row["revision"]) != int(expected_revision):
                raise CatalogConflictError(
                    f"airport revision conflict: expected {expected_revision}, current {row['revision']}"
                )
            refs = int(conn.execute(
                "SELECT COUNT(*) AS c FROM situation_airports WHERE airport_id=?", (airport_id,)
            ).fetchone()["c"])
            if refs:
                raise CatalogReferenceError(
                    f"airport is present in {refs} saved Situation(s); remove it there before deleting the base record"
                )
            conn.execute("DELETE FROM airports WHERE airport_id=?", (airport_id,))

    def create_aircraft_type_versioned(self, item: AircraftType) -> Dict[str, Any]:
        try:
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO aircraft_types
                       (aircraft_type_id,name,speed_kmh,max_range_km,reserve_ratio,
                        departure_capacity_occupancy_factor,arrival_capacity_occupancy_factor,
                        revision,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                    (item.aircraft_type_id,item.name,item.speed_kmh,item.max_range_km,item.reserve_ratio,
                     item.departure_capacity_occupancy_factor,item.arrival_capacity_occupancy_factor),
                )
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(f"aircraft type already exists or violates catalog constraints: {item.aircraft_type_id}") from exc
        return next(x for x in self.list_aircraft_types_with_metadata() if x["aircraft_type"]["aircraft_type_id"] == item.aircraft_type_id)

    def delete_aircraft_type_versioned(self, aircraft_type_id: str, *, expected_revision: int) -> None:
        try:
            with self.connect() as conn:
                row = conn.execute("SELECT revision FROM aircraft_types WHERE aircraft_type_id=?", (aircraft_type_id,)).fetchone()
                if row is None:
                    raise KeyError(f"aircraft type not found: {aircraft_type_id}")
                if int(row["revision"]) != int(expected_revision):
                    raise CatalogConflictError("aircraft type revision conflict")
                conn.execute("DELETE FROM aircraft_types WHERE aircraft_type_id=?", (aircraft_type_id,))
        except sqlite3.IntegrityError as exc:
            raise CatalogReferenceError(f"aircraft type is referenced by current airport/mission data: {aircraft_type_id}") from exc

    def save_aircraft_type_versioned(
        self, item: AircraftType, *, expected_revision: Optional[int] = None
    ) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM aircraft_types WHERE aircraft_type_id=?", (item.aircraft_type_id,)).fetchone()
            if row is None:
                raise KeyError(f"aircraft type not found: {item.aircraft_type_id}")
            if expected_revision is not None and int(row["revision"]) != int(expected_revision):
                raise CatalogConflictError("aircraft type revision conflict")
            conn.execute(
                """
                UPDATE aircraft_types SET name=?, speed_kmh=?, max_range_km=?, reserve_ratio=?,
                    departure_capacity_occupancy_factor=?, arrival_capacity_occupancy_factor=?,
                    revision=revision+1, updated_at=CURRENT_TIMESTAMP
                WHERE aircraft_type_id=?
                """,
                (item.name, item.speed_kmh, item.max_range_km, item.reserve_ratio,
                 item.departure_capacity_occupancy_factor, item.arrival_capacity_occupancy_factor,
                 item.aircraft_type_id),
            )
            meta = conn.execute(
                "SELECT revision, created_at, updated_at FROM aircraft_types WHERE aircraft_type_id=?",
                (item.aircraft_type_id,),
            ).fetchone()
        return {"aircraft_type": item.to_dict(), "metadata": dict(meta)}

    def list_aircraft_types_with_metadata(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM aircraft_types ORDER BY aircraft_type_id").fetchall()
        return [
            {
                "aircraft_type": AircraftType(
                    aircraft_type_id=row["aircraft_type_id"], name=row["name"], speed_kmh=row["speed_kmh"],
                    max_range_km=row["max_range_km"], reserve_ratio=row["reserve_ratio"],
                    departure_capacity_occupancy_factor=row["departure_capacity_occupancy_factor"],
                    arrival_capacity_occupancy_factor=row["arrival_capacity_occupancy_factor"],
                ).to_dict(),
                "metadata": {"revision": int(row["revision"]), "created_at": row["created_at"], "updated_at": row["updated_at"]},
            }
            for row in rows
        ]

    def create_resource_type_versioned(self, item: ResourceType) -> Dict[str, Any]:
        try:
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO resource_types
                       (resource_type_id,name,category,unit,revision,created_at,updated_at)
                       VALUES (?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                    (item.resource_type_id,item.name,item.category,item.unit),
                )
        except sqlite3.IntegrityError as exc:
            raise CatalogConflictError(f"resource type already exists or violates catalog constraints: {item.resource_type_id}") from exc
        return next(x for x in self.list_resource_types_with_metadata() if x["resource_type"]["resource_type_id"] == item.resource_type_id)

    def save_resource_type_versioned(self, item: ResourceType, *, expected_revision: int) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM resource_types WHERE resource_type_id=?", (item.resource_type_id,)).fetchone()
            if row is None:
                raise KeyError(f"resource type not found: {item.resource_type_id}")
            if int(row["revision"]) != int(expected_revision):
                raise CatalogConflictError("resource type revision conflict")
            conn.execute(
                """UPDATE resource_types SET name=?,category=?,unit=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                   WHERE resource_type_id=?""",
                (item.name,item.category,item.unit,item.resource_type_id),
            )
        return next(x for x in self.list_resource_types_with_metadata() if x["resource_type"]["resource_type_id"] == item.resource_type_id)

    def delete_resource_type_versioned(self, resource_type_id: str, *, expected_revision: int) -> None:
        try:
            with self.connect() as conn:
                row = conn.execute("SELECT revision FROM resource_types WHERE resource_type_id=?", (resource_type_id,)).fetchone()
                if row is None:
                    raise KeyError(f"resource type not found: {resource_type_id}")
                if int(row["revision"]) != int(expected_revision):
                    raise CatalogConflictError("resource type revision conflict")
                conn.execute("DELETE FROM resource_types WHERE resource_type_id=?", (resource_type_id,))
        except sqlite3.IntegrityError as exc:
            raise CatalogReferenceError(f"resource type is referenced by current catalog/airport data: {resource_type_id}") from exc

    def replace_aircraft_resource_requirements(
        self, aircraft_type_id: str, rows: Sequence[AircraftResourceRequirement], *, expected_aircraft_revision: int
    ) -> Dict[str, Any]:
        if any(x.aircraft_type_id != aircraft_type_id for x in rows):
            raise ValueError("all requirement rows must match aircraft_type_id")
        keys = [(x.resource_type_id, x.basis) for x in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate aircraft resource requirement")
        with self.connect() as conn:
            ac = conn.execute("SELECT revision FROM aircraft_types WHERE aircraft_type_id=?", (aircraft_type_id,)).fetchone()
            if ac is None:
                raise KeyError(f"aircraft type not found: {aircraft_type_id}")
            if int(ac["revision"]) != int(expected_aircraft_revision):
                raise CatalogConflictError("aircraft type revision conflict")
            conn.execute("DELETE FROM aircraft_resource_requirements WHERE aircraft_type_id=?", (aircraft_type_id,))
            for item in rows:
                conn.execute(
                    """INSERT INTO aircraft_resource_requirements
                       (aircraft_type_id,resource_type_id,basis,quantity) VALUES (?,?,?,?)""",
                    (item.aircraft_type_id,item.resource_type_id,item.basis,item.quantity),
                )
            conn.execute(
                "UPDATE aircraft_types SET revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE aircraft_type_id=?",
                (aircraft_type_id,),
            )
        ac_meta = next(x for x in self.list_aircraft_types_with_metadata() if x["aircraft_type"]["aircraft_type_id"] == aircraft_type_id)
        reqs = [x.to_dict() for x in self.list_aircraft_resource_requirements() if x.aircraft_type_id == aircraft_type_id]
        return {"aircraft_type": ac_meta["aircraft_type"], "metadata": ac_meta["metadata"], "requirements": reqs}

    def list_resource_types_with_metadata(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM resource_types ORDER BY resource_type_id").fetchall()
        return [
            {
                "resource_type": ResourceType(
                    resource_type_id=row["resource_type_id"], name=row["name"], category=row["category"], unit=row["unit"]
                ).to_dict(),
                "metadata": {"revision": int(row["revision"]), "created_at": row["created_at"], "updated_at": row["updated_at"]},
            }
            for row in rows
        ]

    @staticmethod
    def _summary_counts(old_ids: set[str], new_ids: set[str]) -> Dict[str, int]:
        return {
            "added": len(new_ids - old_ids),
            "updated": len(new_ids & old_ids),
            "deleted": len(old_ids - new_ids),
            "total": len(new_ids),
        }

    def replace_airport_bundles_current(
        self, bundles: Sequence[tuple[AirportBase, Optional[AirportOperationalProfile]]]
    ) -> Dict[str, int]:
        """Atomically replace the current airport master/profile dataset.

        Saved SituationAirport rows are value snapshots and therefore are not rewritten.
        No historical Base Data version is persisted; root ``revision`` remains only an
        optimistic-concurrency token for later single-record editing.
        """
        ids = [airport.airport_id for airport, _ in bundles]
        if len(ids) != len(set(ids)):
            raise CatalogConflictError("bulk airport import contains duplicate airport_id")
        with self.connect() as conn:
            old_ids = {str(r[0]) for r in conn.execute("SELECT airport_id FROM airports").fetchall()}
            for airport, profile in bundles:
                self._write_airport(conn, airport, is_create=(airport.airport_id not in old_ids))
                if profile is None:
                    conn.execute("DELETE FROM airport_operational_profiles WHERE airport_id=?", (airport.airport_id,))
                else:
                    self._write_profile(conn, profile)
            new_ids = set(ids)
            for airport_id in sorted(old_ids - new_ids):
                conn.execute("DELETE FROM airports WHERE airport_id=?", (airport_id,))
        return self._summary_counts(old_ids, set(ids))

    def replace_aircraft_types_current(self, items: Sequence[AircraftType]) -> Dict[str, int]:
        ids = [x.aircraft_type_id for x in items]
        if len(ids) != len(set(ids)):
            raise CatalogConflictError("bulk aircraft type import contains duplicate aircraft_type_id")
        try:
            with self.connect() as conn:
                old_ids = {str(r[0]) for r in conn.execute("SELECT aircraft_type_id FROM aircraft_types").fetchall()}
                for item in items:
                    if item.aircraft_type_id in old_ids:
                        conn.execute(
                            """UPDATE aircraft_types SET name=?,speed_kmh=?,max_range_km=?,reserve_ratio=?,
                               departure_capacity_occupancy_factor=?,arrival_capacity_occupancy_factor=?,
                               revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE aircraft_type_id=?""",
                            (item.name,item.speed_kmh,item.max_range_km,item.reserve_ratio,
                             item.departure_capacity_occupancy_factor,item.arrival_capacity_occupancy_factor,
                             item.aircraft_type_id),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO aircraft_types
                               (aircraft_type_id,name,speed_kmh,max_range_km,reserve_ratio,
                                departure_capacity_occupancy_factor,arrival_capacity_occupancy_factor,
                                revision,created_at,updated_at)
                               VALUES (?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                            (item.aircraft_type_id,item.name,item.speed_kmh,item.max_range_km,item.reserve_ratio,
                             item.departure_capacity_occupancy_factor,item.arrival_capacity_occupancy_factor),
                        )
                new_ids = set(ids)
                for item_id in sorted(old_ids - new_ids):
                    conn.execute("DELETE FROM aircraft_types WHERE aircraft_type_id=?", (item_id,))
        except sqlite3.IntegrityError as exc:
            raise CatalogReferenceError(
                "replacement aircraft type dataset omits an ID still referenced by current Base Data or a saved Situation"
            ) from exc
        return self._summary_counts(old_ids, set(ids))

    def replace_resource_types_current(self, items: Sequence[ResourceType]) -> Dict[str, int]:
        ids = [x.resource_type_id for x in items]
        if len(ids) != len(set(ids)):
            raise CatalogConflictError("bulk resource type import contains duplicate resource_type_id")
        try:
            with self.connect() as conn:
                old_ids = {str(r[0]) for r in conn.execute("SELECT resource_type_id FROM resource_types").fetchall()}
                for item in items:
                    if item.resource_type_id in old_ids:
                        conn.execute(
                            """UPDATE resource_types SET name=?,category=?,unit=?,revision=revision+1,
                               updated_at=CURRENT_TIMESTAMP WHERE resource_type_id=?""",
                            (item.name,item.category,item.unit,item.resource_type_id),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO resource_types
                               (resource_type_id,name,category,unit,revision,created_at,updated_at)
                               VALUES (?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                            (item.resource_type_id,item.name,item.category,item.unit),
                        )
                new_ids = set(ids)
                for item_id in sorted(old_ids - new_ids):
                    conn.execute("DELETE FROM resource_types WHERE resource_type_id=?", (item_id,))
        except sqlite3.IntegrityError as exc:
            raise CatalogReferenceError(
                "replacement resource type dataset omits an ID still referenced by current Base Data or a saved Situation"
            ) from exc
        return self._summary_counts(old_ids, set(ids))

    def replace_aircraft_resource_requirements_current(
        self, items: Sequence[AircraftResourceRequirement]
    ) -> Dict[str, int]:
        keys = [(x.aircraft_type_id, x.resource_type_id, x.basis) for x in items]
        if len(keys) != len(set(keys)):
            raise CatalogConflictError("bulk requirement import contains duplicate key")
        with self.connect() as conn:
            old_keys = {tuple(r) for r in conn.execute(
                "SELECT aircraft_type_id,resource_type_id,basis FROM aircraft_resource_requirements"
            ).fetchall()}
            conn.execute("DELETE FROM aircraft_resource_requirements")
            for item in items:
                conn.execute(
                    "INSERT INTO aircraft_resource_requirements (aircraft_type_id,resource_type_id,basis,quantity) VALUES (?,?,?,?)",
                    (item.aircraft_type_id,item.resource_type_id,item.basis,item.quantity),
                )
            touched_aircraft = sorted({x.aircraft_type_id for x in items} | {str(k[0]) for k in old_keys})
            for aircraft_type_id in touched_aircraft:
                conn.execute(
                    "UPDATE aircraft_types SET revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE aircraft_type_id=?",
                    (aircraft_type_id,),
                )
        new_keys = set(keys)
        return {
            "added": len(new_keys - old_keys),
            "updated": len(new_keys & old_keys),
            "deleted": len(old_keys - new_keys),
            "total": len(new_keys),
        }

