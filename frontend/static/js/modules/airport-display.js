const AIRPORT_ROLE_DISPLAY = {
  civil: { className: 'airport-role-civil', label: '民用' },
  military: { className: 'airport-role-military', label: '军用' },
  joint: { className: 'airport-role-joint', label: '军民两用' },
};

function airportRoleDisplay(role) {
  return AIRPORT_ROLE_DISPLAY[role] || { className: 'airport-role-unknown', label: '未标注' };
}

export function airportRoleClass(role) {
  return airportRoleDisplay(role).className;
}

export function airportRoleLabel(role) {
  return airportRoleDisplay(role).label;
}

export function shortAirportName(name) {
  const text = String(name || '').trim();
  return text.replace(/\s+(?:International\s+|General\s+)?(?:Airport|Air Base)$/i, '').trim() || text;
}

export function airportMapLabel(airport) {
  return shortAirportName(airport?.airport_name);
}

export function airportMapTooltip(airport) {
  return String(airport?.airport_name || '').trim();
}
