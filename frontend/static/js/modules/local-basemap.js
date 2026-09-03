const TRANSPARENT_TILE = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';

const SOURCE_LAYERS = [
  {
    source: 'world', pane: 'local-basemap-world', zIndex: 180,
    bounds: [[-85.0511287798066, -180], [85.0511287798066, 180]],
    minZoom: 2, maxNativeZoom: 7, maxZoom: 15,
  },
  {
    source: 'eastasia', pane: 'local-basemap-eastasia', zIndex: 190,
    bounds: [[4.214943141390654, 68.90625], [55.7765730186677, 150.46875]],
    minZoom: 8, maxNativeZoom: 11, maxZoom: 15,
  },
  {
    source: 'china_east', pane: 'local-basemap-china-east', zIndex: 200,
    bounds: [[17.97873309555615, 106.962890625], [42.5530802889558, 124.013671875]],
    minZoom: 12, maxNativeZoom: 13, maxZoom: 15,
  },
  {
    source: 'japan', pane: 'local-basemap-japan', zIndex: 210,
    bounds: [[23.88583769986199, 122.783203125], [46.55886030311718, 150.732421875]],
    minZoom: 12, maxNativeZoom: 14, maxZoom: 15,
  },
  {
    source: 'korea', pane: 'local-basemap-korea', zIndex: 220,
    bounds: [[32.842673631954305, 124.453125], [38.95940879245423, 130.078125]],
    minZoom: 12, maxNativeZoom: 14, maxZoom: 15,
  },
  {
    source: 'taiwan', pane: 'local-basemap-taiwan', zIndex: 230,
    bounds: [[22.998851594142913, 119.1796875], [25.562265014427506, 122.34375]],
    minZoom: 12, maxNativeZoom: 15, maxZoom: 15,
  },
];

export function createLocalBasemap(map, tileTemplate) {
  const L = globalThis.L;
  if (!L || !map) throw new Error('Leaflet local basemap is unavailable');
  if (!tileTemplate || !tileTemplate.includes('{source}')) {
    throw new Error('Local basemap tile template must include {source}');
  }

  return SOURCE_LAYERS.map((spec) => {
    const pane = map.getPane(spec.pane) || map.createPane(spec.pane);
    pane.style.zIndex = String(spec.zIndex);
    const layer = L.tileLayer(tileTemplate.replace('{source}', spec.source), {
      pane: spec.pane,
      bounds: spec.bounds,
      minZoom: spec.minZoom,
      maxNativeZoom: spec.maxNativeZoom,
      maxZoom: spec.maxZoom,
      noWrap: true,
      keepBuffer: 4,
      updateWhenIdle: false,
      updateWhenZooming: true,
      errorTileUrl: TRANSPARENT_TILE,
    });
    layer.addTo(map);
    return layer;
  });
}

export function destroyLocalBasemap(layers) {
  for (const layer of layers || []) layer.remove?.();
}
