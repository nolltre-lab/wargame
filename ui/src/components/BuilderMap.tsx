import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { emptyFC } from '../lib/geo';
import { loadSidcImage } from '../lib/milsymbol';
import type { BuilderUnit, Objective } from '../types';

const MAP_STYLE = 'https://demotiles.maplibre.org/style.json';
const INITIAL_CENTER: [number, number] = [26.5, 58.8];
const INITIAL_ZOOM = 6;

type MapData = Parameters<maplibregl.GeoJSONSource['setData']>[0];
const md = (o: unknown) => o as MapData;

const OBJ_ICON: Record<string, string> = {
  airfield: '✈', port: '⚓', city: '■', bridge: '═', maritime: '◆', base: '★',
};

interface Props {
  units: BuilderUnit[];
  objectives: Objective[];
  selectedUnitId: string | null;
  isPlacing: boolean;         // true = crosshair cursor, clicks place a unit
  onMapClick: (lat: number, lon: number) => void;
  onUnitClick: (id: string) => void;
}

export function BuilderMap({ units, objectives, selectedUnitId, isPlacing, onMapClick, onUnitClick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const loadedRef = useRef<Set<string>>(new Set());
  const objMarkersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
  const clickedUnitRef = useRef(false);
  const [mapReady, setMapReady] = useState(false);

  // Keep callback refs so map event handlers never go stale
  const onMapClickRef = useRef(onMapClick);
  const onUnitClickRef = useRef(onUnitClick);
  onMapClickRef.current = onMapClick;
  onUnitClickRef.current = onUnitClick;

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

      // Selection highlight
      src('sel');
      lay({ id: 'sel-ring', type: 'circle', source: 'sel',
        paint: { 'circle-radius': 28, 'circle-color': 'transparent',
          'circle-stroke-color': '#00ffff', 'circle-stroke-width': 2,
          'circle-stroke-opacity': 0.85 } });

      // Unit icons
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

      // Click: unit layer first, then map
      map.on('click', 'unit-symbols', (e) => {
        clickedUnitRef.current = true;
        const id = e.features?.[0]?.properties?.id as string | undefined;
        if (id) onUnitClickRef.current(id);
      });
      map.on('click', (e) => {
        if (clickedUnitRef.current) { clickedUnitRef.current = false; return; }
        onMapClickRef.current(e.lngLat.lat, e.lngLat.lng);
      });
      map.on('mouseenter', 'unit-symbols', () => {
        if (!isPlacingRef.current) map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'unit-symbols', () => {
        map.getCanvas().style.cursor = isPlacingRef.current ? 'crosshair' : '';
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

  // Keep isPlacing in a ref so the stale closure in map event handlers can read it
  const isPlacingRef = useRef(isPlacing);
  useEffect(() => {
    isPlacingRef.current = isPlacing;
    const map = mapRef.current;
    if (map) map.getCanvas().style.cursor = isPlacing ? 'crosshair' : '';
  }, [isPlacing]);

  // ── Unit icons ────────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;

    const dpr = Math.ceil(window.devicePixelRatio || 1);
    const needLoad = [...new Set(units.map(u => u.sidc))].filter(s => !loadedRef.current.has(s));

    const push = () => {
      if (!mapRef.current) return;
      (mapRef.current.getSource('units') as maplibregl.GeoJSONSource)?.setData(
        md({
          type: 'FeatureCollection',
          features: units.map(u => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [u.lon, u.lat] },
            properties: { id: u.id, icon_id: `ms-${u.sidc}` },
          })),
        })
      );
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
    (map.getSource('sel') as maplibregl.GeoJSONSource)?.setData(
      md({
        type: 'FeatureCollection',
        features: sel ? [{
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [sel.lon, sel.lat] },
          properties: {},
        }] : [],
      })
    );
  }, [mapReady, units, selectedUnitId]);

  // ── Objective markers (read-only) ─────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    // Remove stale
    const seen = new Set(objectives.map(o => o.id));
    objMarkersRef.current.forEach((m, id) => {
      if (!seen.has(id)) { m.remove(); objMarkersRef.current.delete(id); }
    });
    // Add new
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
