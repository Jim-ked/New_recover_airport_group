from __future__ import annotations

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS airports (
    airport_id TEXT PRIMARY KEY,
    airport_name TEXT NOT NULL,
    facility_type TEXT NOT NULL CHECK (facility_type IN ('large_airport','medium_airport','small_airport')),
    role TEXT NOT NULL CHECK (role IN ('civil','joint','military')),
    icao_code TEXT,
    iata_code TEXT,
    region TEXT,
    municipality TEXT,
    longitude REAL NOT NULL CHECK (longitude >= -180 AND longitude <= 180),
    latitude REAL NOT NULL CHECK (latitude >= -90 AND latitude <= 90),
    elevation_m REAL,
    scheduled_service INTEGER NOT NULL CHECK (scheduled_service IN (0,1)),
    runways_known INTEGER NOT NULL CHECK (runways_known IN (0,1))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_airports_icao
ON airports(icao_code) WHERE icao_code IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_airports_iata
ON airports(iata_code) WHERE iata_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS runways (
    runway_id TEXT PRIMARY KEY,
    airport_id TEXT NOT NULL,
    length_m REAL CHECK (length_m IS NULL OR length_m >= 0),
    width_m REAL CHECK (width_m IS NULL OR width_m >= 0),
    surface TEXT,
    lighted INTEGER CHECK (lighted IS NULL OR lighted IN (0,1)),
    low_ident TEXT,
    low_latitude REAL CHECK (low_latitude IS NULL OR (low_latitude >= -90 AND low_latitude <= 90)),
    low_longitude REAL CHECK (low_longitude IS NULL OR (low_longitude >= -180 AND low_longitude <= 180)),
    low_elevation_m REAL,
    low_heading_deg_true REAL CHECK (low_heading_deg_true IS NULL OR (low_heading_deg_true >= 0 AND low_heading_deg_true <= 360)),
    low_displaced_threshold_m REAL CHECK (low_displaced_threshold_m IS NULL OR low_displaced_threshold_m >= 0),
    high_ident TEXT,
    high_latitude REAL CHECK (high_latitude IS NULL OR (high_latitude >= -90 AND high_latitude <= 90)),
    high_longitude REAL CHECK (high_longitude IS NULL OR (high_longitude >= -180 AND high_longitude <= 180)),
    high_elevation_m REAL,
    high_heading_deg_true REAL CHECK (high_heading_deg_true IS NULL OR (high_heading_deg_true >= 0 AND high_heading_deg_true <= 360)),
    high_displaced_threshold_m REAL CHECK (high_displaced_threshold_m IS NULL OR high_displaced_threshold_m >= 0),
    FOREIGN KEY (airport_id) REFERENCES airports(airport_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_runways_airport ON runways(airport_id);

CREATE TABLE IF NOT EXISTS aircraft_types (
    aircraft_type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    speed_kmh REAL CHECK (speed_kmh IS NULL OR speed_kmh > 0),
    max_range_km REAL CHECK (max_range_km IS NULL OR max_range_km > 0),
    reserve_ratio REAL CHECK (reserve_ratio IS NULL OR (reserve_ratio >= 0 AND reserve_ratio < 1)),
    departure_capacity_occupancy_factor REAL CHECK (departure_capacity_occupancy_factor IS NULL OR departure_capacity_occupancy_factor > 0),
    arrival_capacity_occupancy_factor REAL CHECK (arrival_capacity_occupancy_factor IS NULL OR arrival_capacity_occupancy_factor > 0)
);

CREATE TABLE IF NOT EXISTS resource_types (
    resource_type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('fuel','material','munition')),
    unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aircraft_resource_requirements (
    aircraft_type_id TEXT NOT NULL,
    resource_type_id TEXT NOT NULL,
    basis TEXT NOT NULL CHECK (basis IN ('per_sortie','per_hour')),
    quantity REAL NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (aircraft_type_id, resource_type_id, basis),
    FOREIGN KEY (aircraft_type_id) REFERENCES aircraft_types(aircraft_type_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS airport_operational_profiles (
    airport_id TEXT PRIMARY KEY,
    configuration_complete INTEGER NOT NULL DEFAULT 0 CHECK (configuration_complete IN (0,1)),
    capacity_per_window INTEGER CHECK (capacity_per_window IS NULL OR capacity_per_window >= 0),
    support_level TEXT,
    FOREIGN KEY (airport_id) REFERENCES airports(airport_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS airport_aircraft_support (
    airport_id TEXT NOT NULL,
    aircraft_type_id TEXT NOT NULL,
    initial_quantity INTEGER CHECK (initial_quantity IS NULL OR initial_quantity >= 0),
    tau_reset_windows INTEGER CHECK (tau_reset_windows IS NULL OR tau_reset_windows >= 0),
    PRIMARY KEY (airport_id, aircraft_type_id),
    FOREIGN KEY (airport_id) REFERENCES airport_operational_profiles(airport_id) ON DELETE CASCADE,
    FOREIGN KEY (aircraft_type_id) REFERENCES aircraft_types(aircraft_type_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS airport_resource_stocks (
    airport_id TEXT NOT NULL,
    resource_type_id TEXT NOT NULL,
    quantity REAL CHECK (quantity IS NULL OR quantity >= 0),
    PRIMARY KEY (airport_id, resource_type_id),
    FOREIGN KEY (airport_id) REFERENCES airport_operational_profiles(airport_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
);
"""
