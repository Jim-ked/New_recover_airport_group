SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS situation_damage_scenarios (
    situation_id TEXT NOT NULL,
    damage_scenario_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('low','medium','high','custom')),
    PRIMARY KEY (situation_id, damage_scenario_id),
    FOREIGN KEY (situation_id) REFERENCES situations(situation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS situation_damage_events (
    situation_id TEXT NOT NULL,
    damage_scenario_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    airport_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('airport', 'runway', 'support_element')),
    target_id TEXT,
    damage_type TEXT NOT NULL CHECK (damage_type IN ('aircraft_damage','resource_damage','capacity_damage','navigation_delay')),
    start_slot INTEGER NOT NULL CHECK (start_slot >= 0),
    end_slot INTEGER NOT NULL CHECK (end_slot > start_slot),
    effect_json TEXT NOT NULL CHECK (json_valid(effect_json)),
    recovery_mode TEXT NOT NULL CHECK (recovery_mode IN ('none','instant','average')),
    recovery_duration_slots INTEGER CHECK (recovery_duration_slots IS NULL OR recovery_duration_slots > 0),
    PRIMARY KEY (situation_id, damage_scenario_id, event_id),
    UNIQUE (situation_id, damage_scenario_id, sequence_no),
    FOREIGN KEY (situation_id, damage_scenario_id)
        REFERENCES situation_damage_scenarios(situation_id, damage_scenario_id) ON DELETE CASCADE,
    FOREIGN KEY (situation_id, airport_id)
        REFERENCES situation_airports(situation_id, airport_id) ON DELETE RESTRICT,
    CHECK (
        (target_type = 'airport' AND target_id IS NULL)
        OR (target_type IN ('runway', 'support_element') AND target_id IS NOT NULL AND length(trim(target_id)) > 0)
    )
);
"""
