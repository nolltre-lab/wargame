const R = 6371; // Earth radius km

function destPoint(lat: number, lon: number, bearingDeg: number, distKm: number): [number, number] {
  const d = distKm / R;
  const b = (bearingDeg * Math.PI) / 180;
  const φ1 = (lat * Math.PI) / 180;
  const λ1 = (lon * Math.PI) / 180;
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) + Math.cos(φ1) * Math.sin(d) * Math.cos(b));
  const λ2 = λ1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(φ1), Math.cos(d) - Math.sin(φ1) * Math.sin(φ2));
  return [(φ2 * 180) / Math.PI, (((λ2 * 180) / Math.PI) + 540) % 360 - 180];
}

export type RingFeature = {
  type: 'Feature';
  geometry: { type: 'Polygon'; coordinates: [number, number][][] };
  properties: Record<string, unknown>;
};

export type LineFeature = {
  type: 'Feature';
  geometry: { type: 'LineString'; coordinates: [number, number][] };
  properties: Record<string, unknown>;
};

export type FC<F> = { type: 'FeatureCollection'; features: F[] };

export function emptyFC<F>(): FC<F> {
  return { type: 'FeatureCollection', features: [] };
}

/** Full 360° circle polygon, radius in km. */
export function circlePolygon(
  lat: number, lon: number, radiusKm: number, steps = 64
): RingFeature {
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const [dlat, dlon] = destPoint(lat, lon, (360 / steps) * i, radiusKm);
    ring.push([dlon, dlat]);
  }
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} };
}

/**
 * Forward radar cone: a wedge from the unit position out to radiusKm,
 * centred on headingDeg with ±halfAngleDeg spread.
 */
export function conePolygon(
  lat: number, lon: number, radiusKm: number,
  headingDeg: number, halfAngleDeg = 60, steps = 32
): RingFeature {
  const ring: [number, number][] = [[lon, lat]];
  for (let i = 0; i <= steps; i++) {
    const angle = headingDeg - halfAngleDeg + (halfAngleDeg * 2 / steps) * i;
    const [dlat, dlon] = destPoint(lat, lon, angle, radiusKm);
    ring.push([dlon, dlat]);
  }
  ring.push([lon, lat]);
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} };
}

/** Straight line feature between two positions. */
export function lineFeature(
  fromLat: number, fromLon: number, toLat: number, toLon: number
): LineFeature {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [[fromLon, fromLat], [toLon, toLat]] },
    properties: {},
  };
}

/** Interpolate fraction t [0,1] along a straight line between two geo points. */
export function interpolate(
  fromLat: number, fromLon: number, toLat: number, toLon: number, t: number
): [number, number] {
  return [fromLat + (toLat - fromLat) * t, fromLon + (toLon - fromLon) * t];
}
