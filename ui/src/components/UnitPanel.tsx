import { useState } from 'react';
import { useSimStore } from '../store/simStore';
import type { MissionType, WsOutMessage } from '../types';

const SIDE_COLOR = { blue: '#4488ff', red: '#ff4444' };

const MISSION_LABELS: Record<MissionType, string> = {
  secure:      'SECURE',
  defend:      'DEFEND',
  patrol:      'PATROL (objective)',
  area_patrol: 'PATROL (area)',
  intercept:   'INTERCEPT',
  rtb:         'RTB / REARM',
  escort:      'ESCORT (unit)',
};

const STATUS_LABELS = {
  en_route:   'EN ROUTE',
  on_station: 'ON STATION',
};

const MAG_LABEL: Record<string, string> = { aa: 'A-A', ag: 'A-G', as: 'A-S' };

interface UnitPanelProps {
  onSend: (msg: WsOutMessage) => void;
}

function Bar({
  value, max, color, label, suffix = '',
}: { value: number; max: number; color: string; label: string; suffix?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#4a6a8a', fontSize: 10, marginBottom: 2 }}>
        <span>{label}</span>
        <span style={{ color }}>{Math.round(value)}{suffix} / {Math.round(max)}{suffix}</span>
      </div>
      <div style={{ background: '#0d1628', border: '1px solid #1e2e4a', height: 5 }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, transition: 'width 0.3s ease' }} />
      </div>
    </div>
  );
}

export function UnitPanel({ onSend }: UnitPanelProps) {
  const unit = useSimStore((s) => s.getSelectedUnit());
  const objectives = useSimStore((s) => s.objectives);
  const allUnits = useSimStore((s) => s.units);
  const simTime = useSimStore((s) => s.sim_time);
  const running = useSimStore((s) => s.running);
  const tick = useSimStore((s) => s.tick);
  const getObjective = useSimStore((s) => s.getObjective);

  const [missionType, setMissionType] = useState<MissionType>('secure');
  const [objectiveId, setObjectiveId] = useState<string>('');
  const [targetUnitId, setTargetUnitId] = useState<string>('');

  // Units on same side eligible as escort targets (not self, not destroyed)
  const escortCandidates = unit
    ? allUnits.filter(u => u.side === unit.side && u.id !== unit.id && !u.destroyed)
    : [];

  const formatTime = (iso: string) => {
    if (!iso) return '--';
    try { return new Date(iso).toUTCString().slice(0, 25) + 'Z'; }
    catch { return iso; }
  };

  const needsObjective = !['intercept', 'area_patrol', 'rtb', 'escort'].includes(missionType);
  const needsTargetUnit = missionType === 'escort';

  const assignMission = () => {
    if (!unit || unit.destroyed) return;
    if (needsObjective && !objectiveId) return;
    if (needsTargetUnit && !targetUnitId) return;
    onSend({
      type: 'assign_mission',
      unit_id: unit.id,
      mission_type: missionType,
      objective_id: needsObjective ? objectiveId : undefined,
      target_unit_id: needsTargetUnit ? targetUnitId : undefined,
      patrol_lat: missionType === 'area_patrol' ? unit.lat : undefined,
      patrol_lon: missionType === 'area_patrol' ? unit.lon : undefined,
    });
  };

  const assignRTB = () => {
    if (!unit || unit.destroyed) return;
    onSend({ type: 'assign_mission', unit_id: unit.id, mission_type: 'rtb' });
  };

  const setPendingLoadout = async (loadout: string) => {
    if (!unit) return;
    await fetch(`http://localhost:8000/unit/${unit.id}/loadout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loadout }),
    });
  };

  const clearMission = () => {
    if (!unit) return;
    onSend({ type: 'clear_mission', unit_id: unit.id });
  };

  const hpPct = unit ? Math.max(0, (unit.hp / unit.max_hp) * 100) : 0;
  const hpColor = hpPct > 60 ? '#22cc66' : hpPct > 30 ? '#ddaa22' : '#cc3322';
  const fuelPct = unit?.fuel_pct ?? 100;
  const fuelColor = fuelPct > 50 ? '#22cc66' : fuelPct > 20 ? '#ddaa22' : '#cc3322';

  const magEntries = unit ? Object.entries(unit.magazines ?? {}).filter(([, v]) => v !== undefined) : [];

  return (
    <div style={{
      position: 'absolute', top: 16, left: 16, zIndex: 10,
      background: 'rgba(8, 12, 22, 0.93)',
      border: '1px solid #1e2e4a',
      color: '#c8d8f0',
      fontFamily: '"Courier New", monospace',
      fontSize: 12,
      padding: '10px 14px',
      minWidth: 260,
      maxWidth: 300,
      userSelect: 'none',
    }}>
      <div style={{ color: '#4a6a8a', marginBottom: 8, letterSpacing: 2, fontSize: 10 }}>
        WARGAME SIM  ·  TACTICAL COP
      </div>

      <div style={{ marginBottom: 8, paddingBottom: 8, borderBottom: '1px solid #1e2e4a' }}>
        <span style={{ color: running ? '#44dd77' : '#dd7744' }}>
          {running ? '● RUNNING' : '■ PAUSED'}
        </span>
        <span style={{ marginLeft: 10, color: '#4a6a8a' }}>T+{tick}</span>
        <div style={{ color: '#3a5a7a', fontSize: 10, marginTop: 2 }}>
          {formatTime(simTime)}
        </div>
      </div>

      {unit ? (
        <>
          <div style={{ color: unit.destroyed ? '#666' : SIDE_COLOR[unit.side], fontWeight: 'bold', marginBottom: 6 }}>
            [{unit.side.toUpperCase()}] {unit.name}
            {unit.destroyed && <span style={{ color: '#cc3322', marginLeft: 8 }}>DESTROYED</span>}
          </div>

          {!unit.destroyed && (
            <>
              <Bar value={unit.hp} max={unit.max_hp} color={hpColor} label="INTEGRITY" />
              <Bar value={fuelPct} max={100} color={fuelColor} label="FUEL" suffix="%" />

              {/* Magazine counts */}
              {magEntries.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ color: '#4a6a8a', fontSize: 10, marginBottom: 4 }}>MUNITIONS</div>
                  {magEntries.map(([key, count]) => {
                    const maxFromStore = unit.magazines[key] !== undefined ? undefined : 0;
                    void maxFromStore;
                    const label = MAG_LABEL[key] ?? key.toUpperCase();
                    const isEmpty = count === 0;
                    return (
                      <div key={key} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2, fontSize: 11 }}>
                        <span style={{ color: '#4a6a8a' }}>{label}</span>
                        <span style={{ color: isEmpty ? '#cc3322' : count < 4 ? '#ddaa22' : '#88bbee' }}>
                          {isEmpty ? 'EMPTY' : count}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Loadout picker — shown for multi-loadout units */}
              {(unit.loadout_presets?.length ?? 0) > 1 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ color: '#4a6a8a', fontSize: 10, marginBottom: 4, letterSpacing: 1 }}>
                    LOADOUT
                    {unit.pending_loadout && unit.pending_loadout !== unit.loadout && (
                      <span style={{ color: '#ddaa22', marginLeft: 6 }}>
                        → {unit.pending_loadout} (next rearm)
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {unit.loadout_presets.map(preset => {
                      const isCurrent = preset === unit.loadout;
                      const isPending = preset === unit.pending_loadout && preset !== unit.loadout;
                      return (
                        <button
                          key={preset}
                          onClick={() => setPendingLoadout(preset)}
                          style={{
                            padding: '3px 7px',
                            fontSize: 10,
                            letterSpacing: 0.5,
                            cursor: 'pointer',
                            fontFamily: '"Courier New", monospace',
                            background: isCurrent ? '#001a30' : isPending ? '#1a1200' : '#060c18',
                            border: isCurrent
                              ? '1px solid #4488ff'
                              : isPending
                              ? '1px solid #ddaa22'
                              : '1px solid #1e2e4a',
                            color: isCurrent ? '#4488ff' : isPending ? '#ddaa22' : '#4a6a8a',
                          }}
                        >
                          {isCurrent ? '● ' : isPending ? '→ ' : ''}{preset}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Loadout selection window */}
              {unit.awaiting_loadout && (
                <div style={{
                  background: '#0d1020', border: '1px solid #6644aa',
                  padding: '5px 8px', marginBottom: 8, fontSize: 11, color: '#aa88ff',
                }}>
                  ◈ SELECT LOADOUT · {unit.loadout_selection_ticks_left} tick{unit.loadout_selection_ticks_left !== 1 ? 's' : ''} remaining
                  {unit.pending_loadout && unit.pending_loadout !== unit.loadout
                    ? <div style={{ color: '#ddaa22', fontSize: 10, marginTop: 2 }}>→ {unit.pending_loadout} queued</div>
                    : <div style={{ color: '#6a6a8a', fontSize: 10, marginTop: 2 }}>no change — will rearm with {unit.loadout || 'current'}</div>
                  }
                </div>
              )}

              {/* Rearming indicator */}
              {unit.rearming && (
                <div style={{
                  background: '#0d1628', border: '1px solid #2a4a2a',
                  padding: '5px 8px', marginBottom: 8, fontSize: 11, color: '#22cc66',
                }}>
                  ↩ REARMING · {unit.rearm_ticks_left} tick{unit.rearm_ticks_left !== 1 ? 's' : ''} remaining
                  {unit.pending_loadout && unit.pending_loadout !== unit.loadout && (
                    <div style={{ color: '#ddaa22', fontSize: 10, marginTop: 2 }}>
                      → switching to {unit.pending_loadout}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
            {unit.data_link && (
              <div style={{
                padding: '2px 6px', fontSize: 9, letterSpacing: 1,
                border: '1px solid #2255aa', color: '#4488ff', background: '#050d1a',
              }}>
                ◈ DATA LINK
              </div>
            )}
            <div style={{
              padding: '2px 6px', fontSize: 9, letterSpacing: 1,
              border: unit.emcon ? '1px solid #aa8800' : '1px solid #226622',
              color: unit.emcon ? '#ddaa22' : '#44cc66',
              background: unit.emcon ? '#0d0800' : '#041004',
            }}>
              {unit.emcon ? '◉ EMITTING' : '◎ EMCON'}
            </div>
          </div>

          <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 10 }}>
            <tbody>
              {([
                ['TYPE', `${unit.unit_class.toUpperCase()}${unit.unit_type ? ` / ${unit.unit_type}` : ''}`],
                ['POS', `${unit.lat.toFixed(3)}° ${unit.lon.toFixed(3)}°`],
                ['HDG', `${unit.heading.toFixed(0)}°`],
                ['SPD', `${unit.speed.toFixed(0)} km/h`],
                ['ALT', `${unit.altitude_m?.toFixed(0) ?? '—'} m`],
                ['RCS', `${unit.rcs?.toFixed(3) ?? '—'} m²`],
              ] as [string, string][]).map(([k, v]) => (
                <tr key={k}>
                  <td style={{ color: '#4a6a8a', paddingRight: 8, paddingBottom: 2, whiteSpace: 'nowrap' }}>{k}</td>
                  <td style={{ paddingBottom: 2 }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {unit.mission && !unit.destroyed ? (
            <div style={{
              background: '#0d1628', border: '1px solid #1e2e4a',
              padding: '6px 8px', marginBottom: 10, fontSize: 11,
            }}>
              <div style={{ color: '#4a6a8a', marginBottom: 3 }}>CURRENT MISSION</div>
              <div style={{ color: '#88bbee' }}>
                {MISSION_LABELS[unit.mission.type]}
                {unit.mission.objective_id && (
                  <span style={{ color: '#c8d8f0' }}>
                    {' → '}{getObjective(unit.mission.objective_id)?.name ?? unit.mission.objective_id}
                  </span>
                )}
                {unit.mission.type === 'area_patrol' && unit.mission.patrol_lat != null && (
                  <div style={{ fontSize: 10, color: '#6a8aaa', marginTop: 2 }}>
                    {unit.mission.patrol_lat.toFixed(3)}° {unit.mission.patrol_lon?.toFixed(3)}°
                  </div>
                )}
                {unit.mission.type === 'escort' && unit.mission.target_unit_id && (
                  <span style={{ color: '#c8d8f0' }}>
                    {' → '}{allUnits.find(u => u.id === unit.mission!.target_unit_id)?.name ?? unit.mission.target_unit_id}
                  </span>
                )}
              </div>
              <div style={{ color: unit.mission.status === 'on_station' ? '#44dd77' : '#ddaa44', marginTop: 2 }}>
                {STATUS_LABELS[unit.mission.status]}
              </div>
            </div>
          ) : !unit.destroyed ? (
            <div style={{ color: '#3a5a7a', marginBottom: 10, fontSize: 11 }}>No active mission</div>
          ) : null}

          {!unit.destroyed && !unit.rearming && (
            <div style={{ borderTop: '1px solid #1e2e4a', paddingTop: 8 }}>
              <div style={{ color: '#4a6a8a', marginBottom: 6, fontSize: 10, letterSpacing: 1 }}>
                ASSIGN MISSION
              </div>

              <select
                value={missionType}
                onChange={(e) => setMissionType(e.target.value as MissionType)}
                style={selectStyle}
              >
                {(Object.keys(MISSION_LABELS) as MissionType[])
                  .filter(t => t !== 'rtb')
                  .map((t) => (
                    <option key={t} value={t}>{MISSION_LABELS[t]}</option>
                  ))}
              </select>

              {needsObjective && (
                <select
                  value={objectiveId}
                  onChange={(e) => setObjectiveId(e.target.value)}
                  style={{ ...selectStyle, marginTop: 4 }}
                >
                  <option value="">— select objective —</option>
                  {objectives.map((o) => (
                    <option key={o.id} value={o.id}>{o.name}</option>
                  ))}
                </select>
              )}

              {needsTargetUnit && (
                <select
                  value={targetUnitId}
                  onChange={(e) => setTargetUnitId(e.target.value)}
                  style={{ ...selectStyle, marginTop: 4 }}
                >
                  <option value="">— select escort target —</option>
                  {escortCandidates.map((u) => (
                    <option key={u.id} value={u.id}>{u.name} ({u.unit_type})</option>
                  ))}
                </select>
              )}

              {missionType === 'area_patrol' && unit && (
                <div style={{ fontSize: 10, color: '#4a6a8a', marginTop: 4 }}>
                  center: {unit.lat.toFixed(3)}° {unit.lon.toFixed(3)}°
                </div>
              )}

              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button
                  onClick={assignMission}
                  disabled={needsObjective && !objectiveId}
                  style={btnStyle('#003318', '#22cc66')}
                >
                  ▶ ASSIGN
                </button>
                {unit.mission && (
                  <button onClick={clearMission} style={btnStyle('#1a0a00', '#cc6622')}>
                    ✕ CLEAR
                  </button>
                )}
              </div>

              {/* RTB button — only shown when unit has a home base */}
              {unit.home_base_lat != null && (
                <button
                  onClick={assignRTB}
                  style={{
                    ...btnStyle('#001020', '#4488aa'),
                    width: '100%',
                    marginTop: 6,
                    letterSpacing: 1,
                  }}
                >
                  ↩ RTB / REARM
                </button>
              )}
            </div>
          )}
        </>
      ) : (
        <div style={{ color: '#3a5a7a', fontSize: 11 }}>
          Click a unit symbol to select
        </div>
      )}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  width: '100%',
  background: '#0d1628',
  border: '1px solid #2a3e5a',
  color: '#c8d8f0',
  fontFamily: '"Courier New", monospace',
  fontSize: 12,
  padding: '4px 6px',
};

const btnStyle = (bg: string, border: string): React.CSSProperties => ({
  flex: 1,
  background: bg,
  border: `1px solid ${border}`,
  color: border,
  fontFamily: '"Courier New", monospace',
  fontSize: 12,
  padding: '5px 0',
  cursor: 'pointer',
  letterSpacing: 1,
});
