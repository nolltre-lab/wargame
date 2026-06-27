import { useState, useEffect, useCallback } from 'react';
import { BuilderMap } from './BuilderMap';
import type { BuilderUnit, UnitTypeInfo, UnitClass, Objective, TheaterInfo } from '../types';
import { sidcForUnit } from '../lib/milsymbol';

function nearestObjective(lat: number, lon: number, objs: Objective[], types: Objective['type'][]) {
  const candidates = objs.filter(o => types.includes(o.type));
  if (candidates.length === 0) return null;
  return candidates.reduce((best, o) =>
    Math.hypot(o.lat - lat, o.lon - lon) < Math.hypot(best.lat - lat, best.lon - lon) ? o : best
  );
}

function defaultLoadout(info: UnitTypeInfo | undefined): string {
  if (!info?.loadout_presets) return '';
  return Object.keys(info.loadout_presets)[0] ?? '';
}

const API = 'http://localhost:8000';

// ─── Styles ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: '"Courier New", monospace' };
const SIDE_COLOR = { blue: '#4488ff', red: '#ff4444' };
const CLASS_LABEL: Record<UnitClass, string> = { air: 'AIR', ground: 'GND', naval: 'NVL' };

// ─── Component ────────────────────────────────────────────────────────────────

interface Props {
  onExit: () => void;
}

export function ScenarioBuilder({ onExit }: Props) {
  const [unitTypes, setUnitTypes] = useState<Record<string, UnitTypeInfo>>({});
  const [theaters, setTheaters] = useState<TheaterInfo[]>([]);
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [classFilter, setClassFilter] = useState<UnitClass | 'all'>('all');
  const [activeSide, setActiveSide] = useState<'blue' | 'red'>('blue');
  const [placingType, setPlacingType] = useState<string | null>(null);
  const [placedUnits, setPlacedUnits] = useState<BuilderUnit[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [objectives, setObjectives] = useState<Objective[]>([]);
  const [maritimeCorridors, setMaritimeCorridors] = useState<number[][]>([]);
  const [scenarioName, setScenarioName] = useState('my_scenario');
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [flyTo, setFlyTo] = useState<{ center: [number, number]; zoom: number } | null>(null);

  // ── Escape cancels active placement ──────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') setPlacingType(null); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // ── Load unit type library + scenario list on mount ───────────────────────
  useEffect(() => {
    fetch(`${API}/unit-types`)
      .then(r => r.json())
      .then((data: Record<string, UnitTypeInfo>) => setUnitTypes(data))
      .catch(console.error);

    fetch(`${API}/scenarios`)
      .then(r => r.json())
      .then((list: string[]) => setScenarios(list))
      .catch(console.error);

    fetch(`${API}/theaters`)
      .then(r => r.json())
      .then((list: TheaterInfo[]) => setTheaters(list))
      .catch(console.error);
  }, []);

  // ── Load existing scenario ────────────────────────────────────────────────
  const loadScenario = useCallback(async (filename: string) => {
    try {
      const data = await fetch(`${API}/scenarios/${encodeURIComponent(filename)}`).then(r => r.json());
      const loaded: BuilderUnit[] = (data.units ?? []).map((u: Record<string, unknown>) => ({
        id:            u.id as string,
        side:          u.side as 'blue' | 'red',
        unit_type:     u.unit_type as string,
        unit_class:    u.unit_class as UnitClass,
        sidc:          u.sidc as string,
        lat:           u.lat as number,
        lon:           u.lon as number,
        name:          u.name as string,
        airborne:      (u.airborne as boolean | undefined) ?? (u.unit_class !== 'air'),
        loadout:       (u.loadout as string | undefined) ?? '',
        home_base_lat: (u.home_base_lat as number | undefined) ?? null,
        home_base_lon: (u.home_base_lon as number | undefined) ?? null,
      }));
      setPlacedUnits(loaded);
      setObjectives(data.objectives ?? []);
      setMaritimeCorridors(data.maritime_corridors ?? []);
      setScenarioName(filename.replace(/\.json$/, ''));
      setSelectedId(null);
      setPlacingType(null);
    } catch (e) {
      console.error('Failed to load scenario', e);
    }
  }, []);

  // ── Load theater base map ────────────────────────────────────────────────
  const loadTheater = useCallback(async (id: string) => {
    try {
      const data = await fetch(`${API}/theaters/${encodeURIComponent(id)}`).then(r => r.json());
      setObjectives(data.objectives ?? []);
      setMaritimeCorridors(data.maritime_corridors ?? []);
      setPlacedUnits([]);
      setSelectedId(null);
      setPlacingType(null);
      setScenarioName(`${id}_scenario`);
      // Fly the map to the theater center [lon, lat]
      if (data.center && data.zoom) {
        setFlyTo({ center: data.center as [number, number], zoom: data.zoom });
      }
    } catch (e) {
      console.error('Failed to load theater', e);
    }
  }, []);

  // ── Place unit on map click ───────────────────────────────────────────────
  const handleMapClick = useCallback((lat: number, lon: number) => {
    if (!placingType) return;
    const info = unitTypes[placingType];
    if (!info) return;

    let placeLat = lat;
    let placeLon = lon;
    const isAir = info.unit_class === 'air';

    // Ground-based air units must snap to the nearest airfield / base objective.
    // If no suitable objective exists, block placement and warn.
    if (isAir) {
      const airfields = objectives.filter(o => o.type === 'airfield' || o.type === 'base');
      if (airfields.length === 0) {
        alert('No airfields or bases on the map. Add an airfield objective first, or set the unit to AIRBORNE.');
        return;
      }
      // Snap to the nearest airfield (Euclidean on lat/lon is close enough at this scale)
      const nearest = airfields.reduce((best, o) => {
        const d = Math.hypot(o.lat - lat, o.lon - lon);
        return d < Math.hypot(best.lat - lat, best.lon - lon) ? o : best;
      });
      placeLat = nearest.lat;
      placeLon = nearest.lon;
    }

    const count = placedUnits.filter(u => u.unit_type === placingType && u.side === activeSide).length + 1;
    const sidc = sidcForUnit(placingType, activeSide);

    // Determine home base
    let homeBaseLat: number | null = null;
    let homeBaseLon: number | null = null;
    if (isAir) {
      // Air: home base is the snapped airfield
      homeBaseLat = placeLat;
      homeBaseLon = placeLon;
    } else if (info.unit_class === 'naval') {
      // Naval: nearest port or base
      const port = nearestObjective(placeLat, placeLon, objectives, ['port', 'base']);
      if (port) { homeBaseLat = port.lat; homeBaseLon = port.lon; }
      else { homeBaseLat = placeLat; homeBaseLon = placeLon; }
    } else {
      // Ground: rearms in place
      homeBaseLat = placeLat;
      homeBaseLon = placeLon;
    }

    const unit: BuilderUnit = {
      id:            `${activeSide}_${placingType}_${Date.now()}`,
      side:          activeSide,
      unit_type:     placingType,
      unit_class:    info.unit_class,
      sidc,
      lat:           placeLat,
      lon:           placeLon,
      name:          `${info.display_name} (${activeSide === 'blue' ? 'Blue' : 'Red'}) #${count}`,
      airborne:      isAir ? false : true,
      loadout:       defaultLoadout(info),
      home_base_lat: homeBaseLat,
      home_base_lon: homeBaseLon,
    };
    setPlacedUnits(prev => [...prev, unit]);
  }, [placingType, placedUnits, activeSide, unitTypes, objectives]);

  // ── Select a placed unit ─────────────────────────────────────────────────
  const handleUnitClick = useCallback((id: string) => {
    setSelectedId(prev => (prev === id ? null : id));
  }, []);

  // ── Move a placed unit (drag on map) ──────────────────────────────────────
  const handleUnitMove = useCallback((id: string, lat: number, lon: number) => {
    setPlacedUnits(prev => prev.map(u => {
      if (u.id !== id) return u;
      // Ground-based air units must stay at an airfield — snap to nearest
      if (u.unit_class === 'air' && !u.airborne) {
        const airfields = objectives.filter(o => o.type === 'airfield' || o.type === 'base');
        if (airfields.length > 0) {
          const nearest = airfields.reduce((best, o) =>
            Math.hypot(o.lat - lat, o.lon - lon) < Math.hypot(best.lat - lat, best.lon - lon) ? o : best
          );
          return { ...u, lat: nearest.lat, lon: nearest.lon };
        }
      }
      return { ...u, lat, lon };
    }));
  }, [objectives]);

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    setPlacedUnits(prev => prev.filter(u => u.id !== selectedId));
    setSelectedId(null);
  }, [selectedId]);

  // ── Save ──────────────────────────────────────────────────────────────────
  const save = useCallback(async () => {
    setSaveStatus('saving');
    const payload = {
      name: scenarioName,
      start_time: '2024-06-15T04:00:00Z',
      tick_duration_seconds: 60,
      maritime_corridors: maritimeCorridors,
      objectives,
      units: placedUnits.map(u => ({
        id:            u.id,
        name:          u.name,
        side:          u.side,
        unit_class:    u.unit_class,
        unit_type:     u.unit_type,
        sidc:          u.sidc,
        lat:           u.lat,
        lon:           u.lon,
        airborne:      u.airborne,
        loadout:       u.loadout,
        home_base_lat: u.home_base_lat,
        home_base_lon: u.home_base_lon,
      })),
    };
    try {
      const filename = `${scenarioName.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`;
      await fetch(`${API}/scenarios/${encodeURIComponent(filename)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setSaveStatus('saved');
      // Refresh scenario list
      const list = await fetch(`${API}/scenarios`).then(r => r.json());
      setScenarios(list);
      setTimeout(() => setSaveStatus('idle'), 2000);
    } catch {
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 3000);
    }
  }, [scenarioName, placedUnits, objectives, maritimeCorridors]);

  // ── Load in sim ───────────────────────────────────────────────────────────
  const loadInSim = useCallback(async () => {
    await save();
    // The filename the save endpoint writes: base name + .json (dots preserved by backend)
    const filename = `${scenarioName.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`;
    const res = await fetch(`${API}/sim/load`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: filename }),
    });
    if (res.ok) {
      onExit();
    } else {
      const err = await res.json().catch(() => ({}));
      alert(`Failed to load scenario: ${err.detail ?? res.status}`);
    }
  }, [save, scenarioName, onExit]);

  // ── Filtered unit type list ───────────────────────────────────────────────
  const filteredTypes = Object.entries(unitTypes).filter(([, info]) =>
    classFilter === 'all' || info.unit_class === classFilter
  );

  // ─── Render ───────────────────────────────────────────────────────────────

  const sidebarStyle: React.CSSProperties = {
    width: 260,
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(6, 10, 18, 0.97)',
    borderRight: '1px solid #1e2e4a',
    overflow: 'hidden',
    ...MONO,
    fontSize: 12,
    color: '#8aa8c8',
  };

  const sectionHead: React.CSSProperties = {
    padding: '8px 12px',
    fontSize: 10,
    letterSpacing: 2,
    color: '#4a6a8a',
    borderBottom: '1px solid #1e2e4a',
    borderTop: '1px solid #1e2e4a',
    marginTop: 4,
  };

  const btn = (active: boolean, color = '#4488ff'): React.CSSProperties => ({
    background: active ? `${color}22` : 'transparent',
    border: `1px solid ${active ? color : '#1e2e4a'}`,
    color: active ? color : '#4a6a8a',
    ...MONO,
    fontSize: 11,
    padding: '4px 10px',
    cursor: 'pointer',
    letterSpacing: 1,
  });

  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column' }}>

      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <div style={{
        height: 40, display: 'flex', alignItems: 'center', gap: 12,
        padding: '0 16px',
        background: 'rgba(6, 10, 18, 0.97)',
        borderBottom: '1px solid #1e2e4a',
        ...MONO, fontSize: 12, color: '#4a6a8a', flexShrink: 0,
      }}>
        <button onClick={onExit} style={{ ...btn(false), fontSize: 11 }}>← SIM</button>
        <span style={{ letterSpacing: 2, color: '#6a8aaa' }}>SCENARIO BUILDER</span>
        <div style={{ flex: 1 }} />
        {/* Side toggle */}
        <span style={{ fontSize: 11, color: '#4a6a8a' }}>SIDE:</span>
        {(['blue', 'red'] as const).map(s => (
          <button key={s} onClick={() => setActiveSide(s)} style={btn(activeSide === s, SIDE_COLOR[s])}>
            {s.toUpperCase()}
          </button>
        ))}
      </div>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* ── Sidebar ────────────────────────────────────────────────────── */}
        <div style={sidebarStyle}>

          {/* Base maps / theater picker */}
          <div style={{ ...sectionHead, marginTop: 0, borderTop: 'none' }}>BASE MAPS</div>
          <div style={{ padding: '6px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            {theaters.length === 0 && (
              <div style={{ color: '#3a5a7a', fontSize: 11 }}>Loading…</div>
            )}
            {theaters.map(t => (
              <button
                key={t.id}
                onClick={() => loadTheater(t.id)}
                title={t.description}
                style={{
                  background: '#0a1525',
                  border: '1px solid #1e3a5a',
                  color: '#4a8ac8',
                  ...MONO, fontSize: 11,
                  padding: '5px 10px',
                  cursor: 'pointer',
                  letterSpacing: 1,
                  textAlign: 'left',
                }}
              >
                ◉  {t.name}
              </button>
            ))}
          </div>

          {/* Class filter */}
          <div style={{ display: 'flex', gap: 4, padding: '8px 12px' }}>
            {(['all', 'air', 'ground', 'naval'] as const).map(c => (
              <button key={c} onClick={() => setClassFilter(c)}
                style={{ ...btn(classFilter === c), fontSize: 10, padding: '3px 7px' }}>
                {c === 'all' ? 'ALL' : CLASS_LABEL[c as UnitClass]}
              </button>
            ))}
          </div>

          {/* Unit type list */}
          <div style={{ overflowY: 'auto', flex: '0 0 auto', maxHeight: '38%' }}>
            {filteredTypes.map(([type, info]) => {
              const isActive = placingType === type;
              return (
                <div
                  key={type}
                  onClick={() => setPlacingType(isActive ? null : type)}
                  style={{
                    padding: '6px 12px',
                    cursor: 'pointer',
                    background: isActive ? `${SIDE_COLOR[activeSide]}18` : 'transparent',
                    borderLeft: `3px solid ${isActive ? SIDE_COLOR[activeSide] : 'transparent'}`,
                    color: isActive ? SIDE_COLOR[activeSide] : '#8aa8c8',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  }}
                >
                  <span style={{ fontSize: 11 }}>{info.display_name}</span>
                  <span style={{ fontSize: 10, color: '#4a6a8a' }}>
                    {CLASS_LABEL[info.unit_class]}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Placed units */}
          <div style={sectionHead}>PLACED UNITS ({placedUnits.length})</div>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {placedUnits.length === 0 && (
              <div style={{ padding: '10px 12px', color: '#3a5a7a', fontSize: 11 }}>
                {placingType ? 'Click map to place unit' : 'Select a unit type above'}
              </div>
            )}
            {placedUnits.map(u => (
              <div
                key={u.id}
                onClick={() => handleUnitClick(u.id)}
                style={{
                  padding: '5px 12px', cursor: 'pointer',
                  background: selectedId === u.id ? '#1a2a3a' : 'transparent',
                  borderLeft: `3px solid ${selectedId === u.id ? '#00ffff' : 'transparent'}`,
                  display: 'flex', gap: 8, alignItems: 'center',
                }}
              >
                <span style={{
                  fontSize: 10, padding: '1px 5px',
                  border: `1px solid ${SIDE_COLOR[u.side]}`,
                  color: SIDE_COLOR[u.side],
                }}>
                  {u.side === 'blue' ? 'B' : 'R'}
                </span>
                <span style={{ fontSize: 11, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {unitTypes[u.unit_type]?.display_name ?? u.unit_type}
                </span>
                {u.unit_class === 'air' && (
                  <span style={{ fontSize: 9, color: u.airborne ? '#4488ff' : '#ddaa22', letterSpacing: 0 }}>
                    {u.airborne ? '✈' : '⬛'}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Selected unit actions */}
          {selectedId && (() => {
            const sel = placedUnits.find(u => u.id === selectedId);
            if (!sel) return null;
            const typeInfo = unitTypes[sel.unit_type];
            const presets = typeInfo?.loadout_presets ?? {};
            const presetKeys = Object.keys(presets);
            return (
              <div style={{ padding: '6px 12px', borderTop: '1px solid #1e2e4a', display: 'flex', flexDirection: 'column', gap: 4 }}>
                {/* Airborne toggle for air units */}
                {sel.unit_class === 'air' && (
                  <div style={{ display: 'flex', gap: 4 }}>
                    {([false, true] as const).map(val => (
                      <button
                        key={String(val)}
                        onClick={() => {
                          if (val === false) {
                            const airfields = objectives.filter(o => o.type === 'airfield' || o.type === 'base');
                            if (airfields.length === 0) {
                              alert('No airfields or bases on the map. Add an airfield objective first.');
                              return;
                            }
                            const nearest = airfields.reduce((best, o) => {
                              const d = Math.hypot(o.lat - sel.lat, o.lon - sel.lon);
                              return d < Math.hypot(best.lat - sel.lat, best.lon - sel.lon) ? o : best;
                            });
                            setPlacedUnits(prev => prev.map(u =>
                              u.id === selectedId
                                ? { ...u, airborne: false, lat: nearest.lat, lon: nearest.lon, home_base_lat: nearest.lat, home_base_lon: nearest.lon }
                                : u
                            ));
                          } else {
                            setPlacedUnits(prev => prev.map(u =>
                              u.id === selectedId ? { ...u, airborne: true } : u
                            ));
                          }
                        }}
                        style={{
                          flex: 1, padding: '4px',
                          background: sel.airborne === val ? (val ? '#001a3a' : '#1a1000') : 'transparent',
                          border: `1px solid ${sel.airborne === val ? (val ? '#4488ff' : '#ddaa22') : '#1e2e4a'}`,
                          color: sel.airborne === val ? (val ? '#4488ff' : '#ddaa22') : '#4a6a8a',
                          ...MONO, fontSize: 10, cursor: 'pointer', letterSpacing: 1,
                        }}
                      >
                        {val ? '✈ AIRBORNE' : '⬛ GROUND'}
                      </button>
                    ))}
                  </div>
                )}

                {/* Loadout picker */}
                {presetKeys.length > 0 && (
                  <div>
                    <div style={{ fontSize: 10, color: '#4a6a8a', marginBottom: 3, letterSpacing: 1 }}>LOADOUT</div>
                    <select
                      value={sel.loadout || presetKeys[0]}
                      onChange={e => setPlacedUnits(prev => prev.map(u =>
                        u.id === selectedId ? { ...u, loadout: e.target.value } : u
                      ))}
                      style={{
                        width: '100%', background: '#0a1020', border: '1px solid #1e2e4a',
                        color: '#88bbee', ...MONO, fontSize: 11, padding: '4px 6px',
                      }}
                    >
                      {presetKeys.map(k => (
                        <option key={k} value={k}>{presets[k].label}</option>
                      ))}
                    </select>
                    {/* Show magazine summary for selected loadout */}
                    {(() => {
                      const preset = presets[sel.loadout || presetKeys[0]];
                      if (!preset) return null;
                      const mags = Object.entries(preset.magazines)
                        .filter(([, v]) => v !== undefined && v !== null)
                        .map(([k, v]) => `${k.toUpperCase()}:${v}`)
                        .join('  ');
                      return (
                        <div style={{ fontSize: 10, color: '#4a6a8a', marginTop: 2 }}>
                          {mags}{preset.weapon_km ? `  · ${preset.weapon_km} km` : ''}
                        </div>
                      );
                    })()}
                  </div>
                )}

                <button onClick={deleteSelected} style={{
                  width: '100%', padding: '5px', background: '#1a0505',
                  border: '1px solid #cc2222', color: '#cc4444',
                  ...MONO, fontSize: 11, cursor: 'pointer', letterSpacing: 1,
                }}>
                  ✕  DELETE SELECTED
                </button>
              </div>
            );
          })()}

          {/* Load scenario */}
          <div style={sectionHead}>LOAD SCENARIO</div>
          <div style={{ padding: '8px 12px', display: 'flex', gap: 6 }}>
            <select
              defaultValue=""
              onChange={e => e.target.value && loadScenario(e.target.value)}
              style={{
                flex: 1, background: '#0a1020', border: '1px solid #1e2e4a',
                color: '#8aa8c8', ...MONO, fontSize: 11, padding: '4px',
              }}
            >
              <option value="" disabled>— choose file —</option>
              {scenarios.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* Save */}
          <div style={sectionHead}>SAVE AS</div>
          <div style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <input
              value={scenarioName}
              onChange={e => setScenarioName(e.target.value)}
              placeholder="scenario_name"
              style={{
                background: '#0a1020', border: '1px solid #1e2e4a',
                color: '#8aa8c8', ...MONO, fontSize: 11,
                padding: '5px 8px', outline: 'none',
              }}
            />
            <div style={{ display: 'flex', gap: 6 }}>
              <button onClick={save} style={{
                flex: 1, padding: '5px',
                background: saveStatus === 'saved' ? '#003318' : '#0a1525',
                border: `1px solid ${saveStatus === 'error' ? '#cc2222' : saveStatus === 'saved' ? '#22cc66' : '#1e3a5a'}`,
                color: saveStatus === 'error' ? '#cc4444' : saveStatus === 'saved' ? '#22cc66' : '#4a8ac8',
                ...MONO, fontSize: 11, cursor: 'pointer', letterSpacing: 1,
              }}>
                {saveStatus === 'saving' ? '...' : saveStatus === 'saved' ? '✓ SAVED' : saveStatus === 'error' ? '✗ ERROR' : '💾 SAVE'}
              </button>
              <button onClick={loadInSim} style={{
                flex: 1, padding: '5px',
                background: '#001a10',
                border: '1px solid #226644',
                color: '#44aa77',
                ...MONO, fontSize: 11, cursor: 'pointer', letterSpacing: 1,
              }}>
                ▶ PLAY
              </button>
            </div>
          </div>

        </div>

        {/* ── Map ──────────────────────────────────────────────────────────── */}
        <div style={{ flex: 1, position: 'relative' }}>
          <BuilderMap
            units={placedUnits}
            objectives={objectives}
            selectedUnitId={selectedId}
            isPlacing={!!placingType}
            onMapClick={handleMapClick}
            onUnitClick={handleUnitClick}
            onUnitMove={handleUnitMove}
            flyTo={flyTo}
          />

          {/* Placement hint overlay */}
          {placingType && (
            <div style={{
              position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
              background: 'rgba(6,10,18,0.88)', border: `1px solid ${SIDE_COLOR[activeSide]}`,
              color: SIDE_COLOR[activeSide],
              padding: '6px 16px', ...MONO, fontSize: 11, letterSpacing: 1,
              pointerEvents: 'none',
            }}>
              PLACING: {unitTypes[placingType]?.display_name ?? placingType} [{activeSide.toUpperCase()}]
              — click map · press Esc to cancel
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
