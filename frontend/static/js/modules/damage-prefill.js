// Adjustable engineering presets, not aviation standards. Percentages describe
// remaining capacity. This module has no DOM, persistence or global RNG state.
export const GENERATOR_VERSION = 1;
export const DAMAGE_PRESETS = {
  low: {label: '轻度', stages: [{label: '受损阶段', ratio: [70, 90], duration: [2, 6]}]},
  medium: {label: '中度', stages: [{label: '受损阶段', ratio: [40, 60], duration: [2, 6]}]},
  high: {label: '重度', stages: [{label: '受损阶段', ratio: [10, 30], duration: [2, 6]}]},
  sustained: {label: '持续', stages: [{label: '连续受损阶段', ratio: [40, 60], duration: [8, 16]}]},
  extreme: {label: '极端', stages: [
    {label: '严重受损阶段', ratio: [10, 30], duration: [2, 4]},
    {label: '完全关闭阶段', ratio: null, duration: [1, 4]},
    {label: '部分恢复阶段', ratio: [40, 70], duration: [2, 4]},
  ]},
};

const compareId = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const integer = (value, minimum = 0) => Number.isSafeInteger(value) && value >= minimum;

function validate(parameters) {
  const {kind, scope, start, end, offset, seed, stages} = parameters;
  const preset = DAMAGE_PRESETS[kind];
  if (!preset) throw Error('请选择快捷模板。');
  if (!['manual', 'all', 'random'].includes(scope)) throw Error('请选择机场范围。');
  if (!integer(start) || !integer(end, 1) || end <= start) throw Error('请明确填写合法生成范围：[开始窗, 结束窗)，开始窗不得小于0。');
  if (!integer(offset)) throw Error('机场间错开幅度必须为非负整数。');
  if (typeof seed !== 'string' || !seed.trim()) throw Error('请填写随机种子或自动生成。');
  if (!Array.isArray(stages) || stages.length !== preset.stages.length) throw Error('模板阶段配置不完整。');
  stages.forEach((stage, index) => {
    if (!Array.isArray(stage.duration) || stage.duration.length !== 2 || !stage.duration.every(v => integer(v, 1)) || stage.duration[0] > stage.duration[1]) throw Error('持续时间范围必须为递增的正整数区间。');
    if (preset.stages[index].ratio === null) {
      if (stage.ratio !== null) throw Error('完全关闭阶段剩余容量必须固定为0。');
    } else if (!Array.isArray(stage.ratio) || stage.ratio.length !== 2 || !stage.ratio.every(v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100) || stage.ratio[0] > stage.ratio[1]) {
      throw Error('剩余容量比例范围必须满足 0% ≤ 下限 ≤ 上限 ≤ 100%。');
    }
  });
  const minimum = stages.reduce((sum, stage) => sum + stage.duration[0], 0);
  if (!integer(minimum, 1) || end - start < minimum) throw Error(`生成范围不足以容纳最短完整过程（${minimum}窗），请调整范围或参数。`);
  return minimum;
}

// FNV-1a seed hash + mulberry32: algorithm and consumption order are versioned.
function seededRandom(seed) {
  let value = 2166136261;
  for (const char of `${GENERATOR_VERSION}:${seed}`) {
    value = Math.imul(value ^ char.codePointAt(0), 16777619) >>> 0;
  }
  return () => {
    value = (value + 0x6D2B79F5) >>> 0;
    let result = Math.imul(value ^ (value >>> 15), 1 | value);
    result ^= result + Math.imul(result ^ (result >>> 7), 61 | result);
    return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
  };
}

function decimalFraction(value) {
  const text = String(value).toLowerCase();
  const [mantissa, exponentText] = text.split('e');
  const exponent = exponentText == null ? 0 : Number(exponentText);
  if (!Number.isSafeInteger(exponent)) throw Error('剩余容量比例必须为有限数字。');
  const sign = mantissa.startsWith('-') ? -1n : 1n;
  const unsigned = mantissa.replace(/^[+-]/, '');
  const [whole, fraction = ''] = unsigned.split('.');
  const digits = `${whole}${fraction}`.replace(/^0+(?=\d)/, '') || '0';
  let numerator = sign * BigInt(digits);
  let denominator = 10n ** BigInt(fraction.length);
  if (exponent > 0) numerator *= 10n ** BigInt(exponent);
  if (exponent < 0) denominator *= 10n ** BigInt(-exponent);
  return {numerator, denominator};
}

const ceilDivide = (numerator, denominator) => (numerator + denominator - 1n) / denominator;

// The legal integers form a contiguous set. Keep exact decimal endpoints as
// rational numbers so binary floating point cannot reject a valid capacity.
function capacitySet(capacity, ratio) {
  if (ratio === null) return {first: 0, last: 0};
  const lower = decimalFraction(ratio[0]);
  const upper = decimalFraction(ratio[1]);
  const capacityInteger = BigInt(capacity);
  const lowerDenominator = lower.denominator * 100n;
  const upperDenominator = upper.denominator * 100n;
  let first = Number(ceilDivide(capacityInteger * lower.numerator, lowerDenominator));
  let last = Number((capacityInteger * upper.numerator) / upperDenominator);
  first = Math.max(1, first);
  last = Math.min(capacity - 1, last);
  return {first, last};
}

function candidatesFor(parameters, airports) {
  const eligible = [];
  const ineligible = [];
  const seen = new Set();
  for (const item of [...airports].sort((a, b) => compareId(a.airport.airport_id, b.airport.airport_id))) {
    const airportId = item.airport.airport_id;
    if (seen.has(airportId)) throw Error(`机场编号重复：${airportId}`);
    seen.add(airportId);
    const profile = item.operational_profile;
    const capacity = profile?.capacity_per_window;
    let reason = '';
    let sets = [];
    if (profile?.configuration_complete !== true) reason = '运行配置不完整';
    else if (!integer(capacity, 1)) reason = '原始每窗容量必须为正整数';
    else {
      sets = parameters.stages.map(stage => capacitySet(capacity, stage.ratio));
      if (sets.some(set => set.first > set.last)) reason = '剩余容量比例范围内没有合法的非关闭整数容量';
      else if (parameters.kind === 'extreme' && sets[0].first >= sets[2].last) reason = '不存在部分恢复容量严格高于严重受损容量的合法组合';
    }
    if (reason) ineligible.push({airportId, reason});
    else eligible.push({airportId, sets});
  }
  return {eligible, ineligible};
}

export function generateDamageEvents(parameters, airports) {
  const minimum = validate(parameters);
  const {eligible, ineligible} = candidatesFor(parameters, airports);
  const random = seededRandom(parameters.seed);
  const draw = (first, last) => first + Math.floor(random() * (last - first + 1));
  let selected;
  if (parameters.scope === 'manual') {
    const ids = [...new Set(parameters.airportIds || [])].sort(compareId);
    if (!ids.length) throw Error('请至少手动选择一个机场。');
    const invalid = ids.filter(id => !eligible.some(item => item.airportId === id));
    if (invalid.length) throw Error(invalid.map(id => `${id}：${ineligible.find(item => item.airportId === id)?.reason || '不属于当前情境'}`).join('；'));
    selected = eligible.filter(item => ids.includes(item.airportId));
  } else if (parameters.scope === 'random') {
    if (!integer(parameters.count, 1) || parameters.count > eligible.length) throw Error(`随机选择数量必须为1至${eligible.length}；适用机场不足时不能减少请求数量。${ineligible.map(item => `${item.airportId}：${item.reason}`).join('；')}`);
    selected = [...eligible];
    for (let i = selected.length - 1; i > 0; i--) {
      const j = draw(0, i);
      [selected[i], selected[j]] = [selected[j], selected[i]];
    }
    selected = selected.slice(0, parameters.count).sort((a, b) => compareId(a.airportId, b.airportId));
  } else selected = eligible;
  if (!selected.length) throw Error(`没有适用机场。${ineligible.map(item => `${item.airportId}：${item.reason}`).join('；')}`);

  const {start, end, offset, stages} = parameters;
  const commonStart = draw(start, end - minimum);
  const events = [];
  for (const {airportId, sets} of selected) {
    // Restrict legal choices before sampling. Never clamp a generated event.
    let cursor = draw(Math.max(start, commonStart - offset), Math.min(end - minimum, commonStart + offset));
    let firstCapacity = null;
    stages.forEach((stage, index) => {
      const remainingMinimum = stages.slice(index + 1).reduce((sum, next) => sum + next.duration[0], 0);
      const duration = draw(stage.duration[0], Math.min(stage.duration[1], end - cursor - remainingMinimum));
      let {first, last} = sets[index];
      if (parameters.kind === 'extreme') {
        if (index === 0) last = Math.min(last, sets[2].last - 1);
        if (index === 2) first = Math.max(first, firstCapacity + 1);
      }
      const capacity = draw(first, last);
      if (index === 0) firstCapacity = capacity;
      events.push({
        target: {airport_id: airportId, target_type: 'airport', target_id: null},
        damage_type: 'capacity_damage', start_slot: cursor, end_slot: cursor + duration,
        effect: {closed: capacity === 0, remaining_capacity_per_window: capacity},
        recovery_mode: 'instant', recovery_duration_slots: null,
      });
      cursor += duration;
    });
  }
  return {events, ineligible, commonStart, version: GENERATOR_VERSION};
}

export function numberDamageEvents(events, existing) {
  const ids = new Set(existing.map(event => event.event_id.trim()));
  const sequences = existing.map(event => Number.parseInt(event.sequence, 10)).filter(Number.isSafeInteger);
  let sequence = sequences.reduce((maximum, value) => Math.max(maximum, value), -1) + 1;
  let suffix = 1;
  return events.map(event => {
    while (ids.has(`P${suffix}`)) suffix += 1;
    if (!Number.isSafeInteger(sequence)) throw Error('事件顺序超出安全整数范围。');
    const event_id = `P${suffix++}`;
    ids.add(event_id);
    return {...event, event_id, sequence: sequence++};
  });
}
