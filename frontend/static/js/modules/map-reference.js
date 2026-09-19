const PROVINCE_URL = '/static/gis/china_provinces.geojson';
const REFERENCE_PANE = 'map-reference-provinces';

export function createMapReference(map) {
  let layer = null;
  let removed = false;
  const reference = {
    ready: null,
    add() {
      removed = false;
      layer?.addTo(map);
    },
    remove() {
      removed = true;
      layer?.remove?.();
    },
    destroy() {
      removed = true;
      layer?.remove?.();
      layer = null;
    },
  };

  reference.ready = (async () => {
    try {
      const pane = map.getPane(REFERENCE_PANE) || map.createPane(REFERENCE_PANE);
      pane.style.zIndex = '300';
      pane.style.pointerEvents = 'none';
      const response = await fetch(PROVINCE_URL, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const provinces = await response.json();
      layer = globalThis.L.geoJSON(provinces, {
        pane: REFERENCE_PANE,
        interactive: false,
        style: {
          color: '#a9beca',
          weight: 0.8,
          opacity: 0.58,
          fill: false,
          fillOpacity: 0,
        },
      });
      if (!removed) layer.addTo(map);
    } catch (error) {
      console.error('省级行政边界加载失败，地图已降级为无参考层。', error);
    }
    return reference;
  })();

  return reference;
}

export function destroyMapReference(reference) {
  reference?.destroy?.();
}
