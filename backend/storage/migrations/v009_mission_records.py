SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS mission_records (
    mission_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    longitude REAL NOT NULL CHECK (longitude >= -180 AND longitude <= 180),
    latitude REAL NOT NULL CHECK (latitude >= -90 AND latitude <= 90),
    window_start_slot INTEGER NOT NULL CHECK (window_start_slot >= 0),
    window_end_slot INTEGER NOT NULL CHECK (window_end_slot > window_start_slot)
);

CREATE TABLE IF NOT EXISTS mission_record_aircraft_requirements (
    mission_id TEXT NOT NULL,
    aircraft_type_id TEXT NOT NULL,
    required_sorties INTEGER NOT NULL CHECK (required_sorties > 0),
    tau_work_windows INTEGER NOT NULL CHECK (tau_work_windows >= 0),
    PRIMARY KEY (mission_id, aircraft_type_id),
    FOREIGN KEY (mission_id) REFERENCES mission_records(mission_id) ON DELETE CASCADE,
    FOREIGN KEY (aircraft_type_id) REFERENCES aircraft_types(aircraft_type_id) ON DELETE RESTRICT
);
"""
