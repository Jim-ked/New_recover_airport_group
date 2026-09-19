const HIDDEN_CLASS = 'map-label-hidden';

function overlaps(left, right, padding) {
  return left.left < right.right + padding
    && left.right + padding > right.left
    && left.top < right.bottom + padding
    && left.bottom + padding > right.top;
}

function insideViewport(rect, viewport) {
  return !viewport || (
    rect.right >= viewport.left
    && rect.left <= viewport.right
    && rect.bottom >= viewport.top
    && rect.top <= viewport.bottom
  );
}

export function layoutMapLabels(items, { viewport = null, padding = 3 } = {}) {
  const ranked = (items || [])
    .filter((item) => item?.element)
    .map((item, index) => ({ ...item, index }))
    .sort((left, right) => (
      Number(Boolean(right.forceVisible)) - Number(Boolean(left.forceVisible))
      || Number(right.priority || 0) - Number(left.priority || 0)
      || left.index - right.index
    ));
  const occupied = [];

  for (const item of ranked) item.element.classList.toggle(HIDDEN_CLASS, false);
  for (const item of ranked) {
    const rect = item.element.getBoundingClientRect();
    const hidden = !item.forceVisible && (
      !insideViewport(rect, viewport)
      || occupied.some((other) => overlaps(rect, other, padding))
    );
    item.element.classList.toggle(HIDDEN_CLASS, hidden);
    if (!hidden && insideViewport(rect, viewport)) occupied.push(rect);
  }
}

export function createMapLabelLayout(map) {
  let items = [];
  let frameRequest = null;

  const layout = () => {
    frameRequest = null;
    layoutMapLabels(items, { viewport: map.getContainer().getBoundingClientRect() });
  };
  const schedule = () => {
    if (frameRequest !== null) cancelAnimationFrame(frameRequest);
    frameRequest = requestAnimationFrame(layout);
  };

  map.on('moveend zoomend resize', schedule);
  return {
    setItems(nextItems) {
      items = [...(nextItems || [])];
      schedule();
    },
    layout,
    destroy() {
      map.off('moveend zoomend resize', schedule);
      if (frameRequest !== null) cancelAnimationFrame(frameRequest);
      for (const item of items) item?.element?.classList.toggle(HIDDEN_CLASS, false);
      items = [];
    },
  };
}
