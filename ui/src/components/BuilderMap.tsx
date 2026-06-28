import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { emptyFC, circlePolygon, type RingFeature } from '../lib/geo';
import { loadSidcImage } from '../lib/milsymbol';
import type { BuilderUnit, Objective, RingToggles, UnitTypeInfo } from '../types';

const MAP_STYLE = 'https://demotiles.maplibre.org/style.json';
const INITIAL_CENTER: [number, number] = [26.5, 58.8];
const INITIAL_ZOOM = 6;

type MapData = Parameters<maplibregl.GeoJSONSource['setData']>[0];
const md = (o: unknown) => o as MapData;

// ── Ring helpers (BuilderUnit has no sensor_km — read from unitTypes) ─────────

function builderSensorRings(
  units: BuilderUnit[], unitTypes: Record<string, UnitTypeInfo>,
  show: boolean, sel: string | null,
): MapData {
  const feats: RingFeature[] = [];
  for (const u of units) {
    const sensor_km = unitTypes[u.unit_type]?.sensor_km ?? 0;
    if (sensor_km <= 0) continue;
    const isSel = u.id === sel;
    if (!show && !isSel) continue;
    const f = circlePolygon(u.lat, u.lon, sensor_km);
    f.properties = { side: u.side, selected: isSel };
    feats.push(f);
  }
  return md({ type: 'FeatureCollection', features: feats });
}

function builderWeaponRings(
  units: BuilderUnit[], unitTypes: Record<string, UnitTypeInfo>,
  tgt: 'air' | 'surface', show: boolean, sel: string | null,
): MapData {
  const feats: RingFeature[] = [];
  for (const u of units) {
    const info = unitTypes[u.unit_type];
    if (!info) continue;
    const isSel = u.id === sel;
    if (!show && !isSel) continue;
    const vtargets: string[] = info.valid_targets ?? [];
    const can = tgt === 'air'
      ? vtargets.includes('air')
      : vtargets.includes('ground') || vtargets.includes('naval');
    if (!can) continue;
    // Respect loadout weapon_km override if set
    const preset = u.loadout ? info.loadout_presets?.[u.loadout] : undefined;
    const weapon_km = preset?.weapon_km ?? info.weapon_km ?? 0;
    if (weapon_km <= 0) continue;
    const f = circlePolygon(u.lat, u.lon, weapon_km);
    f.properties = { side: u.side, selected: isSel };
    feats.push(f);
  }
  return md({ type: 'FeatureCollection', features: feats });
}

const OBJ_ICON: Record<string, string> = {
  airfield: '✈', port: '⚓', city: '■', bridge: '═', maritime: '◆', base: '★',
};

interface FlyTo { center: [number, number]; zoom: number; }

interface Props {
  units: BuilderUnit[];
  objectives: Objective[];
  unitTypes: Record<string, UnitTypeInfo>;
  rings: RingToggles;
  selectedUnitId: string | null;
  isPlacing: boolean;
  onMapClick: (lat: number, lon: number) => void;
  onUnitClick: (id: string) => void;
  onUnitMove: (id: string, lat: number, lon: number) => void;
  flyTo?: FlyTo | null;
}

function unitFC(units: BuilderUnit[], overrideId?: string, overrideLat?: number, overrideLon?: number): MapData {
  return md({
    type: 'FeatureCollection',
    features: units.map(u => ({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: u.id === overrideId
          ? [overrideLon!, overrideLat!]
          : [u.lon, u.lat],
      },
      properties: { id: u.id, icon_id: `ms-${u.sidc}` },
    })),
  });
}

export function BuilderMap({
  units, objectives, unitTypes, rings, selectedUnitId, isPlacing,
  onMapClick, onUnitClick, onUnitMove, flyTo,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef<Set<string>>(new Set());
  const objMarkersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
  const [mapReady, setMapReady] = useState(false);

  // Stale-closure refs — updated on every render
  const onMapClickRef = useRef(onMapClick);
  const onUnitClickRef = useRef(onUnitClick);
  const onUnitMoveRef = useRef(onUnitMove);
  const isPlacingRef = useRef(isPlacing);
  const unitsRef = useRef(units);
  const selectedUnitIdRef = useRef(selectedUnitId);
  onMapClickRef.current = onMapClick;
  onUnitClickRef.current = onUnitClick;
  onUnitMoveRef.current = onUnitMove;
  unitsRef.current = units;
  selectedUnitIdRef.current = selectedUnitId;

  // Drag state
  const draggingRef = useRef<{ id: string; hasMoved: boolean } | null>(null);
  const clickedUnitRef = useRef(false);
  const wasDragRef = useRef(false);

  // ── Map init ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    map.on('load', () => {
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

      src('sel');
      lay({ id: 'sel-ring', type: 'circle', source: 'sel',
        paint: { 'circle-radius': 28, 'circle-color': 'transparent',
          'circle-stroke-color': '#00ffff', 'circle-stroke-width': 2,
          'circle-stroke-opacity': 0.85 } });

      src('units');
      lay({ id: 'unit-symbols', type: 'symbol', source: 'units',
        layout: {
          'icon-image': ['get', 'icon_id'],
          'icon-size': 1.0,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-anchor': 'center',
        },
      } as maplibregl.LayerSpecification);

      // ── Drag handlers ────────────────────────────────────────────────────
      map.on('mousedown', 'unit-symbols', (e) => {
        if (isPlacingRef.current) return;
        e.preventDefault();
        const id = e.features?.[0]?.properties?.id as string | undefined;
        if (!id) return;
        draggingRef.current = { id, hasMoved: false };
        map.dragPan.disable();
        map.getCanvas().style.cursor = 'grabbing';
        clickedUnitRef.current = true;
      });

      map.on('mousemove', (e) => {
        const drag = draggingRef.current;
        if (!drag) return;
        drag.hasMoved = true;
        const { lat, lng } = e.lngLat;

        // Move the icon immediately (bypass React state for smooth feel)
        (map.getSource('units') as maplibregl.GeoJSONSource)?.setData(
          unitFC(unitsRef.current, drag.id, lat, lng)
        );

        // Keep selection ring tracking during drag
        if (selectedUnitIdRef.current === drag.id) {
          (map.getSource('sel') as maplibregl.GeoJSONSource)?.setData(md({
            type: 'FeatureCollection',
            features: [{ type: 'Feature',
              geometry: { type: 'Point', coordinates: [lng, lat] },
              properties: {} }],
          }));
        }
      });

      const endDrag = (lat: number, lng: number) => {
        const drag = draggingRef.current;
        if (!drag) return;
        draggingRef.current = null;
        map.dragPan.enable();
        map.getCanvas().style.cursor = '';
        if (drag.hasMoved) {
          wasDragRef.current = true;
          onUnitMoveRef.current(drag.id, lat, lng);
        }
      };

      map.on('mouseup', (e) => endDrag(e.lngLat.lat, e.lngLat.lng));

      // ── Click handlers ───────────────────────────────────────────────────
      map.on('click', 'unit-symbols', (e) => {
        clickedUnitRef.current = true;
        if (wasDragRef.current) { wasDragRef.current = false; return; }
        const id = e.features?.[0]?.properties?.id as string | undefined;
        if (id) onUnitClickRef.current(id);
      });
      map.on('click', (e) => {
        if (clickedUnitRef.current) { clickedUnitRef.current = false; return; }
        onMapClickRef.current(e.lngLat.lat, e.lngLat.lng);
      });

      // ── Cursor ──────────────────────────────────────────────────────────
      map.on('mouseenter', 'unit-symbols', () => {
        if (!isPlacingRef.current) map.getCanvas().style.cursor = 'grab';
      });
      map.on('mouseleave', 'unit-symbols', () => {
        if (!draggingRef.current)
          map.getCanvas().style.cursor = isPlacingRef.current ? 'crosshair' : '';
      });

      setMapReady(true);
    });

    // Release drag if mouse leaves the window
    const cancelDrag = () => {
      if (!draggingRef.current) return;
      draggingRef.current = null;
      const map = mapRef.current;
      if (map) { map.dragPan.enable(); map.getCanvas().style.cursor = ''; }
    };
    window.addEventListener('mouseup', cancelDrag);

    return () => {
      window.removeEventListener('mouseup', cancelDrag);
      objMarkersRef.current.forEach(m => m.remove());
      objMarkersRef.current.clear();
      map.remove();
      mapRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── flyTo ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapReady || !flyTo) return;
    mapRef.current?.flyTo({ center: flyTo.center, zoom: flyTo.zoom, duration: 1500 });
  }, [mapReady, flyTo]);

  // ── isPlacing cursor ──────────────────────────────────────────────────────
  useEffect(() => {
    isPlacingRef.current = isPlacing;
    const map = mapRef.current;
    if (map && !draggingRef.current)
      map.getCanvas().style.cursor = isPlacing ? 'crosshair' : '';
  }, [isPlacing]);

  // ── Unit icons ────────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map || draggingRef.current) return;  // let drag own the source during drag

    const dpr = Math.ceil(window.devicePixelRatio || 1);
    const needLoad = [...new Set(units.map(u => u.sidc))].filter(s => !loadedRef.current.has(s));

    const push = () => {
      if (!mapRef.current) return;
      (mapRef.current.getSource('units') as maplibregl.GeoJSONSource)?.setData(unitFC(units));
    };

    if (needLoad.length === 0) { push(); return; }

    Promise.all(
      needLoad.map(sidc =>
        loadSidcImage(sidc).then(data => {
          if (!map.hasImage(`ms-${sidc}`)) map.addImage(`ms-${sidc}`, data, { pixelRatio: dpr });
          loadedRef.current.add(sidc);
        })
      )
    ).then(push);
  }, [mapReady, units]);

  // ── Selection ring ────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const sel = selectedUnitId ? units.find(u => u.id === selectedUnitId) : null;
    (map.getSource('sel') as maplibregl.GeoJSONSource)?.setData(md({
      type: 'FeatureCollection',
      features: sel ? [{
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [sel.lon, sel.lat] },
        properties: {},
      }] : [],
    }));
  }, [mapReady, units, selectedUnitId]);

  // ── Range rings ───────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const sd = (id: string, data: MapData) =>
      (map.getSource(id) as maplibregl.GeoJSONSource)?.setData(data);
    sd('sensor-rings', builderSensorRings(units, unitTypes, rings.sensor, selectedUnitId));
    sd('air-rings',    builderWeaponRings(units, unitTypes, 'air',     rings.airWeapon,     selectedUnitId));
    sd('srf-rings',    builderWeaponRings(units, unitTypes, 'surface', rings.surfaceWeapon, selectedUnitId));
  }, [mapReady, units, unitTypes, rings, selectedUnitId]);

  // ── Objective markers (read-only) ─────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const seen = new Set(objectives.map(o => o.id));
    objMarkersRef.current.forEach((m, id) => {
      if (!seen.has(id)) { m.remove(); objMarkersRef.current.delete(id); }
    });
    objectives.forEach(obj => {
      if (objMarkersRef.current.has(obj.id)) return;
      const el = document.createElement('div');
      el.style.cssText =
        'width:22px;height:22px;display:flex;align-items:center;justify-content:center;' +
        'font-size:14px;color:#999;text-shadow:0 0 5px #000;pointer-events:none;';
      el.textContent = OBJ_ICON[obj.type] ?? '◇';
      el.title = obj.name;
      const m = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([obj.lon, obj.lat])
        .addTo(map);
      objMarkersRef.current.set(obj.id, m);
    });
  }, [mapReady, objectives]);

  return <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />;
}
