"""Resolve unique names and construct validated domain values without database I/O."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from backend.domain.airport import AirportBase, RunwayBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.catalog import AircraftType, ResourceType, AircraftResourceRequirement
from backend.domain.damage import DamageEvent, DamageScenario
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.situation import ResourceReplenishment, Situation, SituationAirport

from .models import CompiledScenario, ExcelData, ExcelRow, ValidationIssue, ValidationResult


ENUMS = {
    'facility_type': {'大型机场': 'large_airport', '中型机场': 'medium_airport', '小型机场': 'small_airport'},
    'role': {'民用': 'civil', '军民合用': 'joint', '军用': 'military'},
    'category': {'燃油': 'fuel', '航材': 'material', '航弹': 'munition'},
    'basis': {'每小时': 'per_hour', '每架次': 'per_sortie'},
    'damage_type': {'起降能力损毁': 'capacity_damage', '资源损毁': 'resource_damage', '航空器损失': 'aircraft_damage'},
    'target_type': {'机场': 'airport', '跑道': 'runway'},
    'recovery_mode': {'立即恢复': 'instant', '不恢复': 'none'},
}
MASTERS = {
    'airport': ('01_机场', 'airport_id', 'airport_name'),
    'aircraft': ('02_机型', 'aircraft_type_id', 'name'),
    'resource': ('02_资源', 'resource_type_id', 'name'),
    'mission': ('04_任务', 'mission_id', 'name'),
    'scenario': ('05_损毁方案', 'damage_scenario_id', 'name'),
}


class _RowError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


class _Validator:
    def __init__(self, data):
        self.data = data
        self.issues = list(data.issues)
        self.names = {}
        self.ids = {}
        self.seen = defaultdict(set)
        for kind, (sheet, id_key, name_key) in MASTERS.items():
            names, ids = defaultdict(list), defaultdict(list)
            for row in self.rows(sheet):
                name, identity = row.values.get(name_key), row.values.get(id_key)
                # Domain factories report wrong types; indexes never coerce them.
                if isinstance(name, str):
                    names[name].append(row)
                    if len(names[name]) > 1:
                        self.error(row, name_key, f'主表名称重复：{name}')
                if isinstance(identity, str):
                    ids[identity].append(row)
                    if len(ids[identity]) > 1:
                        self.error(row, id_key, f'主表编号重复：{identity}')
            self.names[kind], self.ids[kind] = names, ids

    def rows(self, sheet):
        return self.data.tables.get(sheet, [])

    def error(self, row, key, message):
        leaf = re.sub(r'\[\d+\]', '', key).split('.')[-1]
        aliases = {'aircraft_type_id': 'aircraft_name', 'resource_type_id': 'resource_name', 'end_slot': 'end_slot', 'target_id': 'target_name'}
        label = row.labels.get(leaf) or row.labels.get(aliases.get(leaf)) or key
        number = self.data.metadata_rows.get(label, row.row) if row.sheet == '00_场景说明' else row.row
        self.issues.append(ValidationIssue(row.sheet, number, label, message))

    def attempt(self, row, callback):
        try:
            return callback()
        except ValueError as exc:
            self.error(row, getattr(exc, 'field', 'row'), str(exc))
            return None

    def resolve(self, row, key, kind):
        name = row.values.get(key)
        matches = self.names[kind].get(name, []) if isinstance(name, str) else []
        if not matches:
            raise _RowError(key, f'主表中找不到名称：{name!r}')
        if len(matches) != 1:
            raise _RowError(key, f'名称对应多个对象：{name}')
        return matches[0].values[MASTERS[kind][1]]

    def unique(self, row, group, identity, key):
        if identity in self.seen[group]:
            raise _RowError(key, '重复关系行，禁止静默覆盖')
        self.seen[group].add(identity)

    def enum(self, row, key):
        value = row.values.get(key)
        mapping = ENUMS[key]
        if isinstance(value, str) and value in mapping:
            return mapping[value]
        if isinstance(value, str) and value in mapping.values():
            return value
        raise _RowError(key, f'未知枚举值：{value!r}')

    def boolean(self, row, key):
        value = row.values.get(key)
        if isinstance(value, bool):
            return value
        if value in ('是', 'true'):
            return True
        if value in ('否', 'false'):
            return False
        raise _RowError(key, f'必须填写是/否：{value!r}')

    def catalogs(self):
        aircraft, resources, requirements = [], [], []
        for row in self.rows('02_机型'):
            item = self.attempt(row, lambda: AircraftType.from_mapping(row.values))
            if item:
                aircraft.append(item)
        for row in self.rows('02_资源'):
            def resource():
                return ResourceType.from_mapping({**row.values, 'category': self.enum(row, 'category')})
            item = self.attempt(row, resource)
            if item:
                resources.append(item)
        resource_by_id = {r.resource_type_id: r for r in resources}
        for row in self.rows('02-1_机型资源消耗'):
            def requirement():
                ac = self.resolve(row, 'aircraft_name', 'aircraft')
                rs = self.resolve(row, 'resource_name', 'resource')
                self.unique(row, 'requirements', (ac, rs), 'resource_name')
                item = AircraftResourceRequirement.from_mapping({
                    'aircraft_type_id': ac, 'resource_type_id': rs,
                    'quantity': row.values.get('quantity'), 'basis': self.enum(row, 'basis')})
                if rs in resource_by_id:
                    item.validate_resource(resource_by_id[rs])
                return item
            item = self.attempt(row, requirement)
            if item:
                requirements.append(item)
        return aircraft, resources, requirements

    def airports(self):
        runways, support, stocks, replenishments = (defaultdict(list) for _ in range(4))
        runway_ids = {}
        for row in self.rows('01-1_跑道'):
            def runway():
                ap = self.resolve(row, 'airport_name', 'airport')
                label = row.values.get('runway_label')
                if not isinstance(label, str) or not label:
                    raise _RowError('runway_label', '跑道编号不能为空')
                self.unique(row, 'runways', (ap, label), 'runway_label')
                # Human runway designations contain '/'; generate a global stable ID.
                identity = 'RW_' + hashlib.sha256(f'{ap}\0{label}'.encode()).hexdigest()[:32]
                raw = {k: row.values.get(k) for k in ('length_m', 'width_m', 'surface')}
                raw['runway_id'] = identity
                raw['lighted'] = None if row.values.get('lighted') is None else self.boolean(row, 'lighted')
                ends = label.split('/')
                raw['low_end'] = {'ident': ends[0]}
                raw['high_end'] = {'ident': ends[1]} if len(ends) == 2 else None
                item = RunwayBase.from_mapping(raw, index=0)
                runway_ids[(ap, label)] = identity
                runways[ap].append((row, item))
            self.attempt(row, runway)
        specs = (
            ('03-1_机场机型保障', 'aircraft_name', 'aircraft', 'aircraft_type_id', support, AirportAircraftSupport, ('initial_quantity', 'tau_reset_windows')),
            ('03-2_机场资源库存', 'resource_name', 'resource', 'resource_type_id', stocks, AirportResourceStock, ('initial_quantity', 'replenishment_capacity_per_window', 'storage_capacity')),
            ('03-3_资源补给', 'resource_name', 'resource', 'resource_type_id', replenishments, ResourceReplenishment, ('start_slot', 'end_slot', 'quantity')),
        )
        for sheet, name_key, kind, id_key, dest, factory, fields in specs:
            for row in self.rows(sheet):
                def child():
                    ap = self.resolve(row, 'airport_name', 'airport')
                    identity = self.resolve(row, name_key, kind)
                    if factory is not ResourceReplenishment:
                        self.unique(row, sheet, (ap, identity), name_key)
                    raw = {k: row.values.get(k) for k in fields}
                    raw[id_key] = identity
                    item = factory.from_mapping(raw, index=0)
                    dest[ap].append((row, item))
                self.attempt(row, child)
        profiles = {}
        for row in self.rows('03_机场保障'):
            def profile():
                ap = row.values.get('airport_id')
                matches = self.ids['airport'].get(ap, []) if isinstance(ap, str) else []
                if len(matches) != 1:
                    raise _RowError('airport_id', '机场编号必须对应唯一机场主表对象')
                # This sheet retains an ID; if supplied, the readable name must agree.
                if row.values.get('airport_name') is not None:
                    if self.resolve(row, 'airport_name', 'airport') != ap:
                        raise _RowError('airport_name', '机场名称与编号不一致')
                self.unique(row, 'profiles', ap, 'airport_id')
                raw = {'airport_id': ap, 'capacity_per_window': row.values.get('capacity_per_window'),
                       'aircraft_support': [item.to_dict() for _, item in sorted(support[ap], key=lambda pair: pair[1].aircraft_type_id)],
                       'resource_stocks': [item.to_dict() for _, item in sorted(stocks[ap], key=lambda pair: pair[1].resource_type_id)]}
                profiles[ap] = AirportOperationalProfile.from_mapping(raw, derive_configuration=True)
            self.attempt(row, profile)
        airports = []
        for row in self.rows('01_机场'):
            def airport():
                ap = row.values.get('airport_id')
                raw = {k: v for k, v in row.values.items() if k != 'runway_state'}
                raw.update(facility_type=self.enum(row, 'facility_type'), role=self.enum(row, 'role'),
                           scheduled_service=self.boolean(row, 'scheduled_service'))
                state = row.values.get('runway_state')
                if state not in ('未知', '有跑道', '无跑道'):
                    raise _RowError('runway_state', '跑道状态必须为未知/有跑道/无跑道')
                children = sorted(runways[ap], key=lambda pair: pair[1].runway_id)
                if state != '有跑道' and children:
                    raise _RowError('runway_state', '跑道状态与子表跑道数据冲突')
                if state == '有跑道' and not children:
                    raise _RowError('runway_state', '有跑道时必须提供跑道子表数据')
                raw['runways'] = None if state == '未知' else [item.to_dict() for _, item in children]
                raw['runway_count'] = None if state == '未知' else len(children)
                lengths = [item.length_m for _, item in children if item.length_m is not None]
                raw['max_runway_length_m'] = max(lengths) if lengths else None
                base = AirportBase.from_mapping(raw)
                if ap not in profiles:
                    raise _RowError('airport_id', '缺少该机场的机场保障配置')
                valid_replenishments = []
                for source, item in replenishments[ap]:
                    valid = self.attempt(source, lambda: SituationAirport(base, profiles[ap], (item,)))
                    if valid:
                        valid_replenishments.append((source, item))
                # Check interval overlaps on the source row before aggregate construction.
                previous = defaultdict(list)
                overlaps = False
                for source, item in valid_replenishments:
                    if any(max(item.start_slot, other.start_slot) < min(item.end_slot, other.end_slot)
                           for other in previous[item.resource_type_id]):
                        self.error(source, 'start_slot', '同一机场资源的补给时间窗重叠')
                        overlaps = True
                    previous[item.resource_type_id].append(item)
                if overlaps:
                    return SituationAirport(base, profiles[ap])
                return SituationAirport(base, profiles[ap], tuple(item for _, item in valid_replenishments))
            item = self.attempt(row, airport)
            if item:
                airports.append(item)
        # Match database unique constraints during validate, without opening SQLite.
        for key in ('icao_code', 'iata_code'):
            for row in self.rows('01_机场'):
                code = row.values.get(key)
                if code is not None:
                    self.attempt(row, lambda: self.unique(row, key, code, key))
        return airports, runway_ids

    def missions(self):
        requirements = defaultdict(list)
        for row in self.rows('04-1_任务机型需求'):
            def requirement():
                mission = self.resolve(row, 'mission_name', 'mission')
                ac = self.resolve(row, 'aircraft_name', 'aircraft')
                self.unique(row, 'mission_requirement', (mission, ac), 'aircraft_name')
                item = MissionAircraftRequirement.from_mapping({
                    'aircraft_type_id': ac, 'required_sorties': row.values.get('required_sorties'),
                    'tau_work_windows': row.values.get('tau_work_windows')}, index=0)
                requirements[mission].append(item)
            self.attempt(row, requirement)
        missions = []
        for row in self.rows('04_任务'):
            def mission():
                raw = {k: v for k, v in row.values.items() if k != 'region_category'}
                raw['aircraft_requirements'] = [item.to_dict() for item in sorted(requirements[raw.get('mission_id')], key=lambda item: item.aircraft_type_id)]
                return Mission.from_mapping(raw)
            item = self.attempt(row, mission)
            if item:
                missions.append(item)
        return missions

    def damage(self, airports, runway_ids):
        groups = {}
        for row in self.rows('05-1_损毁事件'):
            def event_row():
                enabled = self.boolean(row, 'enabled')
                scenario_id = self.resolve(row, 'scenario_name', 'scenario')
                ap = self.resolve(row, 'airport_name', 'airport')
                if not enabled:
                    # Disabled effects are omitted, but readable names still obey
                    # the workbook's unique-reference contract.
                    for key, kind in (('resource_name', 'resource'), ('aircraft_name', 'aircraft')):
                        if row.values.get(key) is not None:
                            self.resolve(row, key, kind)
                    return
                damage_type = self.enum(row, 'damage_type')
                target_type = self.enum(row, 'target_type')
                target_id = None
                if target_type == 'runway':
                    target_id = runway_ids.get((ap, row.values.get('target_name')))
                    if target_id is None:
                        raise _RowError('target_name', '目标跑道不在该机场的跑道子表中')
                elif row.values.get('target_name') is not None:
                    raise _RowError('target_name', '机场目标不应填写目标名称')
                raw = {k: row.values.get(k) for k in ('event_id', 'sequence', 'start_slot', 'end_slot')}
                raw.update(damage_type=damage_type, recovery_mode=self.enum(row, 'recovery_mode'),
                           target={'airport_id': ap, 'target_type': target_type, 'target_id': target_id})
                if damage_type == 'capacity_damage':
                    if raw['recovery_mode'] != 'instant':
                        raise _RowError('recovery_mode', '容量关闭必须使用立即恢复 instant')
                    if row.values.get('remaining_capacity_per_window') != 0 or isinstance(row.values.get('remaining_capacity_per_window'), bool):
                        raise _RowError('remaining_capacity_per_window', '关闭期间容量必须等于 0')
                    effect = {'closed': True, 'remaining_capacity_per_window': 0}
                    irrelevant = ('resource_name', 'remaining_quantity', 'aircraft_name', 'aircraft_loss')
                elif damage_type == 'aircraft_damage':
                    ac = self.resolve(row, 'aircraft_name', 'aircraft')
                    effect = {'aircraft_loss': {ac: row.values.get('aircraft_loss')}}
                    irrelevant = ('resource_name', 'remaining_quantity', 'remaining_capacity_per_window')
                else:
                    rs = self.resolve(row, 'resource_name', 'resource')
                    effect = {'inventory_semantics': 'physical_inventory_v1',
                              'physical_loss': {'remaining_quantity': {rs: row.values.get('remaining_quantity')}}}
                    irrelevant = ('aircraft_name', 'aircraft_loss', 'remaining_capacity_per_window')
                for key in irrelevant:
                    if row.values.get(key) is not None:
                        raise _RowError(key, '此损毁类型不使用该字段，禁止静默忽略')
                single = DamageEvent.from_mapping({**raw, 'effect': effect})
                # Validate physical inventory and airport support against the local V5 domain.
                try:
                    Situation('CHECK', '检查', airports=tuple(airports), damage_scenarios=(DamageScenario('CHECK', '检查', 'custom', (single,)),))
                except ValueError as exc:
                    key = 'resource_name' if damage_type == 'resource_damage' else 'aircraft_name' if damage_type == 'aircraft_damage' else 'target_name'
                    raise _RowError(key, str(exc)) from exc
                key = (scenario_id, single.event_id)
                if key not in groups:
                    groups[key] = (row, raw, effect, [row])
                    return
                first_row, first_raw, first_effect, sources = groups[key]
                if first_raw != raw:
                    raise _RowError('event_id', f'同一事件的机场/类型/目标/顺序/时间窗/恢复模式必须一致（首行 {first_row.row}）')
                if damage_type == 'capacity_damage':
                    raise _RowError('event_id', '容量事件不允许重复行')
                block = 'aircraft_loss' if damage_type == 'aircraft_damage' else 'physical_loss'
                values = effect[block] if block == 'aircraft_loss' else effect[block]['remaining_quantity']
                original = first_effect[block] if block == 'aircraft_loss' else first_effect[block]['remaining_quantity']
                if set(values) & set(original):
                    raise _RowError('event_id', '同一损毁事件的机型/资源不能重复')
                original.update(values)
                sources.append(row)
            self.attempt(row, event_row)
        events = defaultdict(list)
        event_rows = {}
        for (scenario_id, event_id), (row, raw, effect, _) in groups.items():
            def merged_event():
                item = DamageEvent.from_mapping({**raw, 'effect': effect})
                self.unique(row, ('event_sequence', scenario_id), item.sequence, 'sequence')
                return item
            item = self.attempt(row, merged_event)
            if item:
                events[scenario_id].append(item)
                event_rows[(scenario_id, event_id)] = row
        scenarios = []
        for row in self.rows('05_损毁方案'):
            def scenario():
                identity = row.values.get('damage_scenario_id')
                ordered = sorted(events[identity], key=lambda e: e.sequence)
                item = DamageScenario.from_mapping({'damage_scenario_id': identity,
                    'name': row.values.get('name'), 'category': 'custom', 'events': [e.to_dict() for e in ordered]})
                # Locate aggregate failures on the row introducing the conflict.
                for index, event in enumerate(ordered):
                    try:
                        partial = DamageScenario(identity, item.name, item.category, tuple(ordered[:index + 1]))
                        Situation('CHECK', '检查', airports=tuple(airports), damage_scenarios=(partial,))
                    except ValueError as exc:
                        source = event_rows[(identity, event.event_id)]
                        self.error(source, getattr(exc, 'field', 'events'), str(exc))
                        return None
                return item
            item = self.attempt(row, scenario)
            if item:
                scenarios.append(item)
        return scenarios

    def run(self):
        meta = self.data.metadata
        scene_row = ExcelRow('00_场景说明', self.data.metadata_rows.get('场景库编号', 4), {},
                             {'situation_id': '场景库编号', 'name': '场景名称'})
        for key in ('场景库编号', '场景名称', '时间窗长度（min）'):
            if meta.get(key) is None:
                self.issues.append(ValidationIssue('00_场景说明', self.data.metadata_rows.get(key, 3), key, '缺少场景说明值'))
        if meta.get('时间窗长度（min）') != 15 or isinstance(meta.get('时间窗长度（min）'), bool):
            self.issues.append(ValidationIssue('00_场景说明', self.data.metadata_rows.get('时间窗长度（min）', 6), '时间窗长度（min）', '当前模板与算法只支持 15 分钟时间窗'))
        if not self.rows('01_机场'):
            self.issues.append(ValidationIssue('01_机场', 3, '机场编号', '场景至少需要一个机场'))
        aircraft, resources, requirements = self.catalogs()
        airports, runway_ids = self.airports()
        missions = self.missions()
        scenarios = self.damage(airports, runway_ids)
        def situation():
            return Situation.create(situation_id=meta.get('场景库编号'), name=meta.get('场景名称'))
        identity = self.attempt(scene_row, situation)
        if identity is None or any(i.severity == 'error' for i in self.issues):
            return ValidationResult(self.issues)
        scene = self.attempt(scene_row, lambda: Situation(identity.situation_id, identity.name,
            airports=tuple(airports), missions=tuple(missions), damage_scenarios=tuple(scenarios)))
        compiled = CompiledScenario(tuple(aircraft), tuple(resources), tuple(requirements), scene) if scene else None
        return ValidationResult(self.issues, compiled)


def validate(data: ExcelData) -> ValidationResult:
    return _Validator(data).run()
