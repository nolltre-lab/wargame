import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useSimStore } from '../store/simStore';
import type { Unit, Objective, CombatEvent, RingToggles } from '../types';
import {
  circlePolygon, conePolygon, lineFeature, interpolate,
  emptyFC, type RingFeature, type LineFeature,
} from '../lib/geo';
import { loadSidcImage } from '../lib/milsymbol';

const MAP_STYLE = 'https://demotiles.maplibre.org/style.json';
const INITIAL_CENTER: [number, number] = [26.5, 58.8];
const INITIAL_ZOOM = 6;
const MISSILE_MS = 900;

// ─── GeoJSON helpers ──────────────────────────────────────────────────────────

type MapData = Parameters<maplibregl.GeoJSONSource['setData']>[0];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const md = (o: unknown) => o as MapData;

function sensorRings(units: Unit[], show: boolean, sel: string | null): MapData {
  const feats: RingFeature[] = [];
  for (const u of units) {
    if (u.destroyed || u.sensor_km <= 0) continue;
    const isSel = u.id === sel;
    if (!show && !isSel) continue;
    // sensor_arc_deg + sensor_bi_cone drive shape:
    //   360°           → full circle
    //   <360 bi_cone   → two side-facing arcs (port + starboard), dead zones fore/aft
    //   <360 single    → one wide forward cone
    //   null           → default (forward cone for air, circle for surface)
    const arc = u.sensor_arc_deg;
    if (arc != null && arc < 360 && u.sensor_bi_cone) {
      // Each arc covers half the total arc, facing perpendicular to heading
      const halfAngle = arc / 4;  // total arc / 2 sides / 2 (half-angle of each cone)
      for (const side of [90, 270] as const) {
        const f = conePolygon(u.lat, u.lon, u.sensor_km, (u.heading + side) % 360, halfAngle);
        f.properties = { side: u.side, selected: isSel };
        feats.push(f);
      }
      continue;
    }
    const f = arc != null
      ? arc >= 360
        ? circlePolygon(u.lat, u.lon, u.sensor_km)
        : conePolygon(u.lat, u.lon, u.sensor_km, u.heading, arc / 2)
      : u.unit_class === 'air'
        ? conePolygon(u.lat, u.lon, u.sensor_km, u.heading)
        : circlePolygon(u.lat, u.lon, u.sensor_km);
    f.properties = { side: u.side, selected: isSel };
    feats.push(f);
  }
  return md({ type: 'FeatureCollection', features: feats });
}

function weaponRings(units: Unit[], tgt: 'air' | 'surface', show: boolean, sel: string | null): MapData {
  const feats: RingFeature[] = [];
  for (const u of units) {
    if (u.destroyed || u.weapon_km <= 0) continue;
    const isSel = u.id === sel;
    if (!show && !isSel) continue;
    const can = tgt === 'air'
      ? u.valid_targets.includes('air')
      : u.valid_targets.includes('ground') || u.valid_targets.includes('naval');
    if (!can) continue;
    const f = circlePolygon(u.lat, u.lon, u.weapon_km);
    f.properties = { side: u.side, selected: isSel };
    feats.push(f);
  }
  return md({ type: 'FeatureCollection', features: feats });
}

function unitFC(units: Unit[]): MapData {
  return md({
    type: 'FeatureCollection',
    features: units.map(u => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [u.lon, u.lat] },
      properties: {
        id: u.id, name: u.name, side: u.side,
        icon_id: `ms-${u.sidc}`,
        destroyed: u.destroyed,
      },
    })),
  });
}

function selectedFC(units: Unit[], sel: string | null): MapData {
  const u = sel ? units.find(x => x.id === sel && !x.destroyed) : null;
  return md({
    type: 'FeatureCollection',
    features: u ? [{
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [u.lon, u.lat] },
      properties: {},
    }] : [],
  });
}

// ─── Missile animation ────────────────────────────────────────────────────────

interface Missile { fromLat: number; fromLon: number; toLat: number; toLon: number; startTime: number; color: string; }

function missileColor(ac: string, tc: string) {
  if (ac === 'air' && tc === 'air') return '#00ffff';
  if (tc === 'air') return '#cc44ff';
  if (ac === 'air') return '#ffdd00';
  return '#ff6600';
}

function missileFC(anims: Missile[], now: number): MapData {
  const feats: LineFeature[] = anims.map(a => {
    const t = Math.min(1, (now - a.startTime) / MISSILE_MS);
    const [lat, lon] = interpolate(a.fromLat, a.fromLon, a.toLat, a.toLon, t);
    const f = lineFeature(a.fromLat, a.fromLon, lat, lon);
    f.properties = { color: a.color };
    return f;
  });
  return md({ type: 'FeatureCollection', features: feats });
}

// ─── Objective markers (HTML — static position, fine as markers) ──────────────

const OBJ_ICON: Record<string, string> = {
  airfield: '✈', port: '⚓', city: '■', bridge: '═', maritime: '◆', base: '★',
};
const SIDE_COLOR: Record<string, string> = { blue: '#4488ff', red: '#ff4444' };

function createObjEl(obj: Objective): HTMLDivElement {
  const el = document.createElement('div');
  el.style.cssText =
    `width:24px;height:24px;display:flex;align-items:center;justify-content:center;` +
    `font-size:15px;color:${obj.controlling_side ? SIDE_COLOR[obj.controlling_side] : '#999'};` +
    `text-shadow:0 0 5px #000,0 0 10px #000;pointer-events:none;user-select:none;`;
  el.textContent = OBJ_ICON[obj.type] ?? '◇';
  el.title = obj.name;
  return el;
}

// ─── Component ────────────────────────────────────────────────────────────────

interface MapViewProps {
  rings: RingToggles;
}

export function MapView({ rings }: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef<Set<string>>(new Set()); // loaded SIDC image IDs
  const objMarkersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
  const missilesRef = useRef<Missile[]>([]);
  const rafRef = useRef<number | null>(null);
  const clickedRef = useRef(false);
  const [mapReady, setMapReady] = useState(false);

  const allUnits = useSimStore(s => s.units);
  const perspective = useSimStore(s => s.perspective);
  const blueDetected = useSimStore(s => s.blue_detected);
  const redDetected = useSimStore(s => s.red_detected);
  const objectives = useSimStore(s => s.objectives);
  const selectedUnitId = useSimStore(s => s.selectedUnitId);
  const latestEvents = useSimStore(s => s.latestEvents);
  const selectUnit = useSimStore(s => s.selectUnit);

  // Apply fog-of-war perspective filter
  const units = useMemo(() => {
    if (perspective === 'god') return allUnits;
    const mySide = perspective;
    const detectedSet = new Set(perspective === 'blue' ? blueDetected : redDetected);
    return allUnits.filter(u => u.side === mySide || detectedSet.has(u.id));
  }, [allUnits, perspective, blueDetected, redDetected]);

  // ── Map init ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapContainer.current) return;
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    map.on('load', () => {
      // helper aliases
      const src = (id: string) =>
        map.addSource(id, { type: 'geojson', data: md(emptyFC()) });
      const lay = (spec: maplibregl.LayerSpecification) => map.addLayer(spec);

      // Sensor rings
      src('sensor-rings');
      lay({ id: 'sensor-fill', type: 'fill', source: 'sensor-rings',
        paint: { 'fill-color': ['case', ['==', ['get', 'side'], 'blue'], '#4488ff', '#ff4444'], 'fill-opacity': 0.04 } });
      lay({ id: 'sensor-line', type: 'line', source: 'sensor-rings',
        paint: { 'line-color': ['case', ['==', ['get', 'side'], 'blue'], '#4488ff', '#ff4444'],
          'line-width': ['case', ['get', 'selected'], 1.5, 0.8], 'line-opacity': 0.55,
          'line-dasharray': [4, 3] } });

      // Air weapon rings
      src('air-rings');
      lay({ id: 'air-fill', type: 'fill', source: 'air-rings',
        paint: { 'fill-color': '#ff8800', 'fill-opacity': 0.04 } });
      lay({ id: 'air-line', type: 'line', source: 'air-rings',
        paint: { 'line-color': '#ff8800',
          'line-width': ['case', ['get', 'selected'], 1.5, 0.8], 'line-opacity': 0.65 } });

      // Surface weapon rings
      src('srf-rings');
      lay({ id: 'srf-fill', type: 'fill', source: 'srf-rings',
        paint: { 'fill-color': '#ff3300', 'fill-opacity': 0.04 } });
      lay({ id: 'srf-line', type: 'line', source: 'srf-rings',
        paint: { 'line-color': '#ff3300',
          'line-width': ['case', ['get', 'selected'], 1.5, 0.8], 'line-opacity': 0.65,
          'line-dasharray': [6, 3] } });

      // Missiles
      src('missiles');
      lay({ id: 'missile-line', type: 'line', source: 'missiles',
        paint: { 'line-color': ['get', 'color'], 'line-width': 1.8, 'line-opacity': 0.9 } });
      lay({ id: 'missile-glow', type: 'line', source: 'missiles',
        paint: { 'line-color': ['get', 'color'], 'line-width': 6, 'line-opacity': 0.18, 'line-blur': 4 } });

      // Selection ring (circle layer, coordinate-exact like all GeoJSON)
      src('sel-unit');
      lay({ id: 'sel-ring', type: 'circle', source: 'sel-unit',
        paint: { 'circle-radius': 28, 'circle-color': 'transparent',
          'circle-stroke-color': '#00ffff', 'circle-stroke-width': 2,
          'circle-stroke-opacity': 0.85 } });

      // Unit icons (symbol layer — coordinate system identical to rings)
      src('units');
      lay({ id: 'unit-symbols', type: 'symbol', source: 'units',
        layout: {
          'icon-image': ['get', 'icon_id'],
          'icon-size': 1.0,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-anchor': 'center',
        },
        paint: {
          'icon-opacity': ['case', ['get', 'destroyed'], 0.3, 1.0],
        },
      } as maplibregl.LayerSpecification);

      // ✕ overlay for destroyed units
      lay({ id: 'unit-x', type: 'symbol', source: 'units',
        filter: ['==', ['get', 'destroyed'], true],
        layout: {
          'text-field': '✕',
          'text-size': 18,
          'text-allow-overlap': true,
          'text-ignore-placement': true,
          'text-anchor': 'center',
        },
        paint: {
          'text-color': '#ff2200',
          'text-halo-color': '#000', 'text-halo-width': 1.5,
          'text-opacity': 0.9,
        },
      } as maplibregl.LayerSpecification);

      // Click / hover handlers
      map.on('click', 'unit-symbols', (e) => {
        clickedRef.current = true;
        const props = e.features?.[0]?.properties;
        if (props && !props.destroyed) selectUnit(props.id as string);
      });
      map.on('click', () => {
        if (clickedRef.current) { clickedRef.current = false; return; }
        selectUnit(null);
      });
      map.on('mouseenter', 'unit-symbols', (e) => {
        if (!e.features?.[0]?.properties?.destroyed)
          map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'unit-symbols', () => {
        map.getCanvas().style.cursor = '';
      });

      setMapReady(true);
    });

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      objMarkersRef.current.forEach(m => m.remove());
      objMarkersRef.current.clear();
      map.remove();
      mapRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Rings + selection ring ─────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const sd = (id: string, data: MapData) =>
      (map.getSource(id) as maplibregl.GeoJSONSource)?.setData(data);
    sd('sensor-rings', sensorRings(units, rings.sensor, selectedUnitId));
    sd('air-rings',    weaponRings(units, 'air', rings.airWeapon, selectedUnitId));
    sd('srf-rings',    weaponRings(units, 'surface', rings.surfaceWeapon, selectedUnitId));
    sd('sel-unit',     selectedFC(units, selectedUnitId));
  }, [mapReady, units, rings, selectedUnitId]);

  // ── Unit icons (async load images then update GeoJSON) ────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map || units.length === 0) return;

    const dpr = Math.ceil(window.devicePixelRatio || 1);
    const needLoad = [...new Set(units.map(u => u.sidc))]
      .filter(s => !loadedRef.current.has(s));

    const push = () => {
      if (!mapRef.current) return;
      (mapRef.current.getSource('units') as maplibregl.GeoJSONSource)?.setData(unitFC(units));
    };

    if (needLoad.length === 0) { push(); return; }

    Promise.all(
      needLoad.map(sidc =>
        loadSidcImage(sidc).then(data => {
          if (!map.hasImage(`ms-${sidc}`)) {
            map.addImage(`ms-${sidc}`, data, { pixelRatio: dpr });
          }
          loadedRef.current.add(sidc);
        })
      )
    ).then(push);
  }, [mapReady, units]);

  // ── Objective markers (HTML markers are fine for static points) ───────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map || objectives.length === 0) return;
    const seen = new Set(objectives.map(o => o.id));
    objMarkersRef.current.forEach((m, id) => {
      if (!seen.has(id)) { m.remove(); objMarkersRef.current.delete(id); }
    });
    objectives.forEach(obj => {
      const existing = objMarkersRef.current.get(obj.id);
      if (existing) {
        // Update colour when controlling_side changes
        existing.getElement().style.color =
          obj.controlling_side ? SIDE_COLOR[obj.controlling_side] : '#999';
        return;
      }
      const m = new maplibregl.Marker({ element: createObjEl(obj), anchor: 'center' })
        .setLngLat([obj.lon, obj.lat])
        .addTo(map);
      objMarkersRef.current.set(obj.id, m);
    });
  }, [mapReady, objectives]);

  // ── Missile animations ────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapReady || !latestEvents.length) return;
    const umap = new Map(units.map(u => [u.id, u]));
    latestEvents
      .filter((e): e is CombatEvent & { type: 'engagement' } => e.type === 'engagement')
      .forEach(e => {
        const a = umap.get(e.attacker_id ?? '');
        const t = umap.get(e.target_id ?? '');
        if (a && t) missilesRef.current.push({
          fromLat: a.lat, fromLon: a.lon, toLat: t.lat, toLon: t.lon,
          startTime: performance.now(),
          color: missileColor(a.unit_class, t.unit_class),
        });
      });
    if (missilesRef.current.length > 0 && rafRef.current === null) {
      const frame = () => {
        const m = mapRef.current;
        if (!m) return;
        const now = performance.now();
        missilesRef.current = missilesRef.current.filter(a => now - a.startTime < MISSILE_MS);
        (m.getSource('missiles') as maplibregl.GeoJSONSource)?.setData(missileFC(missilesRef.current, now));
        rafRef.current = missilesRef.current.length > 0 ? requestAnimationFrame(frame) : null;
      };
      rafRef.current = requestAnimationFrame(frame);
    }
  }, [mapReady, latestEvents, units]);

  return <div ref={mapContainer} style={{ position: 'absolute', inset: 0 }} />;
}
