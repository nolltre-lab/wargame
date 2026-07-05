import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useSimStore } from '../store/simStore';
import type { Unit, Objective, RingToggles, SimMissile } from '../types';
import {
  circlePolygon, conePolygon, lineFeature,
  emptyFC, type RingFeature, type LineFeature,
} from '../lib/geo';
import { loadSidcImage } from '../lib/milsymbol';

const MAP_STYLE = 'https://demotiles.maplibre.org/style.json';
const INITIAL_CENTER: [number, number] = [26.5, 58.8];
const INITIAL_ZOOM = 6;

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

// ─── Missile rendering ────────────────────────────────────────────────────────

// Missile trail: line from origin to current position
function missileTrailFC(missiles: SimMissile[]): MapData {
  const feats: LineFeature[] = missiles.map(m => {
    const f = lineFeature(m.origin_lat, m.origin_lon, m.lat, m.lon);
    f.properties = { side: m.side, ammo: m.ammo_type };
    return f;
  });
  return md({ type: 'FeatureCollection', features: feats });
}

// Dashed line from selected missile's current position to its target's current position
function missileTargetLineFC(missiles: SimMissile[], units: Unit[], selectedId: string | null): MapData {
  if (!selectedId) return md(emptyFC());
  const m = missiles.find(x => x.id === selectedId);
  if (!m) return md(emptyFC());
  const target = units.find(u => u.id === m.target_id);
  const destLon = target ? target.lon : m.target_lon;
  const destLat = target ? target.lat : m.target_lat;
  return md({
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[m.lon, m.lat], [destLon, destLat]] },
      properties: { side: m.side },
    }],
  });
}

// Missile head: point at current position with heading and side/type info
function missileHeadFC(missiles: SimMissile[]): MapData {
  return md({
    type: 'FeatureCollection',
    features: missiles.map(m => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [m.lon, m.lat] },
      properties: {
        id: m.id,
        side: m.side,
        ammo: m.ammo_type,
        heading: m.heading,
        icon: `missile-${m.side}-${m.ammo_type}`,
      },
    })),
  });
}

// Draw a small arrowhead canvas image for missile icons.
// The arrow points north (heading=0). MapLibre rotates it per icon-rotate.
function makeMissileIcon(color: string, glowColor: string): ImageData {
  const w = 10, h = 18;
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d')!;
  // glow
  ctx.shadowColor = glowColor;
  ctx.shadowBlur = 4;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(w / 2, 0);    // tip (north)
  ctx.lineTo(w, h * 0.85); // back-right
  ctx.lineTo(w / 2, h * 0.6); // centre notch
  ctx.lineTo(0, h * 0.85); // back-left
  ctx.closePath();
  ctx.fill();
  return ctx.getImageData(0, 0, w, h);
}

const _MISSILE_COLORS: Record<string, Record<string, string>> = {
  blue: { aa: '#00eeff', ag: '#ffdd00', as: '#ff8800' },
  red:  { aa: '#ff00cc', ag: '#ffdd00', as: '#ff3300' },
};

// ─── Objective markers (HTML — static position, fine as markers) ──────────────

const OBJ_ICON: Record<string, string> = {
  airfield: '✈', port: '⚓', city: '■', bridge: '═', maritime: '◆', base: '★',
};
const SIDE_COLOR: Record<string, string> = { blue: '#4488ff', red: '#ff4444' };

function ensureObjPopupStyle() {
  if (document.getElementById('obj-popup-style')) return;
  const s = document.createElement('style');
  s.id = 'obj-popup-style';
  s.textContent = `
    .obj-popup.maplibregl-popup { z-index: 999; }
    .obj-popup .maplibregl-popup-content {
      background: #070e1a !important; border: 1px solid #1e3a5a !important;
      border-radius: 0 !important; padding: 8px 12px 10px !important;
      box-shadow: 0 4px 18px rgba(0,0,0,0.75) !important; min-width: 140px;
    }
    .obj-popup .maplibregl-popup-tip { display: none !important; }
    .obj-popup .maplibregl-popup-close-button {
      color: #4a6a8a !important; font-size: 15px !important;
      top: 2px !important; right: 6px !important; line-height: 1 !important;
    }
    .obj-popup .maplibregl-popup-close-button:hover { color: #8aa8c8 !important; }
  `;
  document.head.appendChild(s);
}

function objPopupHTML(obj: Objective): string {
  const icon = OBJ_ICON[obj.type] ?? '◇';
  const sideCol = obj.controlling_side ? (SIDE_COLOR[obj.controlling_side] ?? '#5a7a9a') : '#5a7a9a';
  const sideLabel = obj.controlling_side ? obj.controlling_side.toUpperCase() : 'NEUTRAL';
  return `<div style="font-family:'Courier New',monospace;font-size:11px;color:#8aa8c8;line-height:1.8;">` +
    `<div style="font-size:12px;color:#aaccee;letter-spacing:1px;margin-bottom:3px;padding-right:14px;">${obj.name}</div>` +
    `<div style="color:#6688aa;">${icon}  ${obj.type.toUpperCase()}</div>` +
    `<div style="color:${sideCol};">${sideLabel}</div>` +
    (obj.country ? `<div style="color:#4a6a8a;font-size:10px;letter-spacing:1px;">${obj.country.toUpperCase()}</div>` : '') +
    `</div>`;
}

// ─── Territory overlay ────────────────────────────────────────────────────────

const API = 'http://localhost:8000';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type GeoJsonFC = { type: 'FeatureCollection'; features: any[] };

function buildTerritoryFC(raw: GeoJsonFC | null, objectives: Objective[], show: boolean): MapData {
  if (!raw || !show) return md(emptyFC());
  const countryToSide: Record<string, string> = {};
  for (const obj of objectives) {
    if (obj.country && obj.controlling_side && !countryToSide[obj.country.toLowerCase()]) {
      countryToSide[obj.country.toLowerCase()] = obj.controlling_side;
    }
  }
  return md({
    type: 'FeatureCollection',
    features: raw.features.map(f => ({
      ...f,
      properties: { ...f.properties, side: countryToSide[f.properties.name ?? ''] ?? 'neutral' },
    })),
  });
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
  const objPopupRef = useRef<maplibregl.Popup | null>(null);
  const objectivesRef = useRef<Objective[]>([]);
  const missileIconsLoadedRef = useRef(false);
  const clickedRef = useRef(false);
  const [mapReady, setMapReady] = useState(false);
  const [territoriesRaw, setTerritoriesRaw] = useState<GeoJsonFC | null>(null);

  const allUnits = useSimStore(s => s.units);
  const perspective = useSimStore(s => s.perspective);
  const blueDetected = useSimStore(s => s.blue_detected);
  const redDetected = useSimStore(s => s.red_detected);
  const blueDetectedMissiles = useSimStore(s => s.blue_detected_missiles);
  const redDetectedMissiles = useSimStore(s => s.red_detected_missiles);
  const allMissiles = useSimStore(s => s.missiles);
  const objectives = useSimStore(s => s.objectives);
  const selectedUnitId = useSimStore(s => s.selectedUnitId);
  const selectUnit = useSimStore(s => s.selectUnit);
  const selectedMissileId = useSimStore(s => s.selectedMissileId);
  const selectMissile = useSimStore(s => s.selectMissile);

  // Keep objectives ref current so popup click handler reads fresh controlling_side
  objectivesRef.current = objectives;

  // Fetch territory polygons once (cached by browser)
  useEffect(() => {
    fetch(`${API}/territories`)
      .then(r => r.json())
      .then(setTerritoriesRaw)
      .catch(() => {/* optional overlay — ignore errors */});
  }, []);

  // Apply fog-of-war perspective filter for units
  const units = useMemo(() => {
    if (perspective === 'god') return allUnits;
    const mySide = perspective;
    const detectedSet = new Set(perspective === 'blue' ? blueDetected : redDetected);
    return allUnits.filter(u => u.side === mySide || detectedSet.has(u.id));
  }, [allUnits, perspective, blueDetected, redDetected]);

  // Apply fog-of-war perspective filter for missiles
  const missiles = useMemo(() => {
    if (perspective === 'god') return allMissiles;
    const detectedSet = new Set(perspective === 'blue' ? blueDetectedMissiles : redDetectedMissiles);
    return allMissiles.filter(m => m.side === perspective || detectedSet.has(m.id));
  }, [allMissiles, perspective, blueDetectedMissiles, redDetectedMissiles]);

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

      // Territory boundaries (12nm sea/airspace)
      src('territory');
      lay({ id: 'territory-fill', type: 'fill', source: 'territory',
        paint: {
          'fill-color': ['match', ['get', 'side'],
            'blue', '#4488ff', 'red', '#ff4444', '#aaaaaa'],
          'fill-opacity': 0.07,
        } });
      lay({ id: 'territory-line', type: 'line', source: 'territory',
        paint: {
          'line-color': ['match', ['get', 'side'],
            'blue', '#4488ff', 'red', '#ff4444', '#888888'],
          'line-width': 1.2, 'line-opacity': 0.55, 'line-dasharray': [5, 4],
        } });

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

      // ── Missile icons — register arrowhead images per side × ammo type ────────
      const SIDES = ['blue', 'red'] as const;
      const AMMO_TYPES = ['aa', 'ag', 'as'] as const;
      for (const side of SIDES) {
        for (const ammo of AMMO_TYPES) {
          const key = `missile-${side}-${ammo}`;
          const color = _MISSILE_COLORS[side]?.[ammo] ?? '#ffffff';
          const glow = side === 'blue' ? '#0044ff' : '#ff0000';
          const img = makeMissileIcon(color, glow);
          if (!map.hasImage(key)) map.addImage(key, img, { pixelRatio: 2 });
        }
      }
      missileIconsLoadedRef.current = true;

      // Missile trail (line from origin to current position)
      src('missile-trail');
      lay({ id: 'missile-trail-glow', type: 'line', source: 'missile-trail',
        paint: {
          'line-color': ['case',
            ['==', ['get', 'side'], 'blue'], '#4488ff',
            '#ff4444'],
          'line-width': 5, 'line-opacity': 0.12, 'line-blur': 3,
        } });
      lay({ id: 'missile-trail-line', type: 'line', source: 'missile-trail',
        paint: {
          'line-color': ['case',
            ['==', ['get', 'side'], 'blue'], '#88aaff',
            '#ff8888'],
          'line-width': 0.8, 'line-opacity': 0.55, 'line-dasharray': [4, 3],
        } });

      // Missile head (icon at current position, rotated by heading)
      src('missile-head');
      lay({ id: 'missile-icon', type: 'symbol', source: 'missile-head',
        layout: {
          'icon-image': ['get', 'icon'],
          'icon-size': 1.2,
          'icon-rotate': ['get', 'heading'],
          'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
      });

      // Missile-to-target dashed line (shown for selected missile only)
      src('missile-target-line');
      lay({ id: 'missile-target-layer', type: 'line', source: 'missile-target-line',
        paint: {
          'line-color': ['case', ['==', ['get', 'side'], 'blue'], '#00eeff', '#ff44cc'],
          'line-width': 1.5, 'line-opacity': 0.75, 'line-dasharray': [6, 4],
        },
      });

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
      map.on('click', 'missile-icon', (e) => {
        clickedRef.current = true;
        const props = e.features?.[0]?.properties;
        if (props?.id) selectMissile(props.id as string);
      });
      map.on('click', () => {
        if (clickedRef.current) { clickedRef.current = false; return; }
        // Close objective popup on bare canvas click
        objPopupRef.current?.remove();
        objPopupRef.current = null;
        selectUnit(null);
      });
      map.on('mouseenter', 'unit-symbols', (e) => {
        if (!e.features?.[0]?.properties?.destroyed)
          map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'unit-symbols', () => {
        map.getCanvas().style.cursor = '';
      });
      map.on('mouseenter', 'missile-icon', () => {
        map.getCanvas().style.cursor = 'crosshair';
      });
      map.on('mouseleave', 'missile-icon', () => {
        map.getCanvas().style.cursor = '';
      });

      setMapReady(true);
    });

    return () => {
      objMarkersRef.current.forEach(m => m.remove());
      objMarkersRef.current.clear();
      map.remove();
      mapRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Rings + selection ring + territory ────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const sd = (id: string, data: MapData) =>
      (map.getSource(id) as maplibregl.GeoJSONSource)?.setData(data);
    sd('sensor-rings', sensorRings(units, rings.sensor, selectedUnitId));
    sd('air-rings',    weaponRings(units, 'air', rings.airWeapon, selectedUnitId));
    sd('srf-rings',    weaponRings(units, 'surface', rings.surfaceWeapon, selectedUnitId));
    sd('sel-unit',     selectedFC(units, selectedUnitId));
    sd('territory',    buildTerritoryFC(territoriesRaw, objectives, rings.territory));
  }, [mapReady, units, rings, selectedUnitId, territoriesRaw, objectives]);

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

  // ── Objective markers ─────────────────────────────────────────────────────
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
        existing.getElement().style.color =
          obj.controlling_side ? (SIDE_COLOR[obj.controlling_side] ?? '#999') : '#999';
        return;
      }
      const el = document.createElement('div');
      el.style.cssText =
        `width:26px;height:26px;display:flex;align-items:center;justify-content:center;` +
        `font-size:15px;color:${obj.controlling_side ? (SIDE_COLOR[obj.controlling_side] ?? '#999') : '#999'};` +
        `text-shadow:0 0 5px #000,0 0 10px #000;cursor:pointer;user-select:none;`;
      el.textContent = OBJ_ICON[obj.type] ?? '◇';
      el.title = obj.name;
      el.addEventListener('mouseenter', () => { el.style.filter = 'brightness(1.4)'; });
      el.addEventListener('mouseleave', () => { el.style.filter = ''; });
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        objPopupRef.current?.remove();
        const current = objectivesRef.current.find(o => o.id === obj.id) ?? obj;
        ensureObjPopupStyle();
        objPopupRef.current = new maplibregl.Popup({
          closeButton: true, className: 'obj-popup', offset: 14, maxWidth: '240px',
        })
          .setLngLat([obj.lon, obj.lat])
          .setHTML(objPopupHTML(current))
          .addTo(map);
      });
      const m = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([obj.lon, obj.lat])
        .addTo(map);
      objMarkersRef.current.set(obj.id, m);
    });
  }, [mapReady, objectives]);

  // ── Missile rendering (state-driven, updates each tick) ──────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !mapReady || !missileIconsLoadedRef.current) return;
    (m.getSource('missile-trail') as maplibregl.GeoJSONSource)?.setData(missileTrailFC(missiles));
    (m.getSource('missile-head')  as maplibregl.GeoJSONSource)?.setData(missileHeadFC(missiles));
    (m.getSource('missile-target-line') as maplibregl.GeoJSONSource)?.setData(
      missileTargetLineFC(missiles, units, selectedMissileId),
    );
  }, [mapReady, missiles, units, selectedMissileId]);

  return <div ref={mapContainer} style={{ position: 'absolute', inset: 0 }} />;
}
