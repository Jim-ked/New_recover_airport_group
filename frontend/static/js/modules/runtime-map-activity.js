export function collectRuntimeActivity(frames, frameIndex, trailWindows) {
  const source = Array.isArray(frames) ? frames : [];
  const end = Math.max(0, Math.min(source.length - 1, Number(frameIndex) || 0));
  const length = Number.isFinite(trailWindows) ? Math.max(1, Math.floor(trailWindows)) : end + 1;
  const start = Math.max(0, end - length + 1);
  const departures = new Set();
  const returns = new Set();
  const departureSorties = new Map();
  const returnSorties = new Map();

  for (let index = start; index <= end; index += 1) {
    for (const item of source[index]?.departures || []) {
      departures.add(item.path_id);
      departureSorties.set(item.path_id, item.sorties);
    }
    for (const item of source[index]?.returns || []) {
      returns.add(item.path_id);
      returnSorties.set(item.path_id, item.sorties);
    }
  }

  return {
    departures,
    returns,
    departureSorties,
    returnSorties,
    currentDepartures: new Set((source[end]?.departures || []).map((item) => item.path_id)),
    currentReturns: new Set((source[end]?.returns || []).map((item) => item.path_id)),
  };
}
