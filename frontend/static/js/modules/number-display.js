const DISPLAY_LOCALE = 'zh-CN';
const MISSING = '—';

function finiteNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Object.is(value, -0) ? 0 : value;
}

function formatNumber(value, options) {
  const numeric = finiteNumber(value);
  if (numeric === null) return MISSING;
  return new Intl.NumberFormat(DISPLAY_LOCALE, { useGrouping: true, ...options }).format(numeric);
}

export function formatInteger(value) {
  return formatNumber(value, { maximumFractionDigits: 0 });
}

export function formatDecimal(
  value,
  { minimumFractionDigits = 0, maximumFractionDigits = 2 } = {},
) {
  return formatNumber(value, { minimumFractionDigits, maximumFractionDigits });
}

export function formatPercent(value, { digits = 1 } = {}) {
  const numeric = finiteNumber(value);
  if (numeric === null) return MISSING;
  return `${formatNumber(numeric * 100, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

export function formatCoordinate(value) {
  return formatNumber(value, { minimumFractionDigits: 5, maximumFractionDigits: 5 });
}

export function formatDistance(value) {
  const shown = formatNumber(value, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return shown === MISSING ? MISSING : `${shown} km`;
}

export function formatSeconds(value) {
  const shown = formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return shown === MISSING ? MISSING : `${shown}s`;
}

export function formatWeight(value) {
  return formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 3 });
}

export function formatHhi(value) {
  return formatNumber(value, { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}
