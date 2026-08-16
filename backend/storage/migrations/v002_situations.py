from __future__ import annotations

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS situations (
    situation_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS situation_airports (
    situation_id TEXT NOT NULL,
    airport_id TEXT NOT NULL,
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
    runways_known INTEGER NOT NULL CHECK (runways_known IN (0,1)),
    configuration_complete INTEGER NOT NULL CHECK (configuration_complete IN (0,1)),
    capacity_per_window INTEGER CHECK (capacity_per_window IS NULL OR capacity_per_window >= 0),
    support_level TEXT,
    PRIMARY KEY (situation_id, airport_id),
    FOREIGN KEY (situation_id) REFERENCES situations(situation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS situation_runways (
    situation_id TEXT NOT NULL,
    airport_id TEXT NOT NULL,
    runway_id TEXT NOT NULL,
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
    PRIMARY KEY (situation_id, airport_id, runway_id),
    FOREIGN KEY (situation_id, airport_id) REFERENCES situation_airports(situation_id, airport_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS situation_aircraft_support (
    situation_id TEXT NOT NULL,
    airport_id TEXT NOT NULL,
    aircraft_type_id TEXT NOT NULL,
    initial_quantity INTEGER CHECK (initial_quantity IS NULL OR initial_quantity >= 0),
    tau_reset_windows INTEGER CHECK (tau_reset_windows IS NULL OR tau_reset_windows >= 0),
    PRIMARY KEY (situation_id, airport_id, aircraft_type_id),
    FOREIGN KEY (situation_id, airport_id) REFERENCES situation_airports(situation_id, airport_id) ON DELETE CASCADE,
    FOREIGN KEY (aircraft_type_id) REFERENCES aircraft_types(aircraft_type_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS situation_resource_stocks (
    situation_id TEXT NOT NULL,
    airport_id TEXT NOT NULL,
    resource_type_id TEXT NOT NULL,
    quantity REAL CHECK (quantity IS NULL OR quantity >= 0),
    PRIMARY KEY (situation_id, airport_id, resource_type_id),
    FOREIGN KEY (situation_id, airport_id) REFERENCES situation_airports(situation_id, airport_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS situation_missions (
    situation_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    name TEXT NOT NULL,
    longitude REAL NOT NULL CHECK (longitude >= -180 AND longitude <= 180),
    latitude REAL NOT NULL CHECK (latitude >= -90 AND latitude <= 90),
    window_start_slot INTEGER NOT NULL CHECK (window_start_slot >= 0),
    window_end_slot INTEGER NOT NULL CHECK (window_end_slot > window_start_slot),
    PRIMARY KEY (situation_id, mission_id),
    FOREIGN KEY (situation_id) REFERENCES situations(situation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS situation_mission_aircraft_requirements (
    situation_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    aircraft_type_id TEXT NOT NULL,
    required_sorties INTEGER NOT NULL CHECK (required_sorties > 0),
    tau_work_windows INTEGER NOT NULL CHECK (tau_work_windows >= 0),
    PRIMARY KEY (situation_id, mission_id, aircraft_type_id),
    FOREIGN KEY (situation_id, mission_id) REFERENCES situation_missions(situation_id, mission_id) ON DELETE CASCADE,
    FOREIGN KEY (aircraft_type_id) REFERENCES aircraft_types(aircraft_type_id) ON DELETE RESTRICT
);
"""
