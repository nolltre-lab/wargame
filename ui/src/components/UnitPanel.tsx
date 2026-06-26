import { useState } from 'react';
import { useSimStore } from '../store/simStore';
import type { MissionType, WsOutMessage } from '../types';

const SIDE_COLOR = { blue: '#4488ff', red: '#ff4444' };

const MISSION_LABELS: Record<MissionType, string> = {
  secure: 'SECURE',
  defend: 'DEFEND',
  patrol: 'PATROL (objective)',
  area_patrol: 'PATROL (area)',
  intercept: 'INTERCEPT',
};

const STATUS_LABELS = {
  en_route: 'EN ROUTE',
  on_station: 'ON STATION',
};

interface UnitPanelProps {
  onSend: (msg: WsOutMessage) => void;
}

export function UnitPanel({ onSend }: UnitPanelProps) {
  const unit = useSimStore((s) => s.getSelectedUnit());
  const objectives = useSimStore((s) => s.objectives);
  const simTime = useSimStore((s) => s.sim_time);
  const running = useSimStore((s) => s.running);
  const tick = useSimStore((s) => s.tick);
  const getObjective = useSimStore((s) => s.getObjective);

  const [missionType, setMissionType] = useState<MissionType>('secure');
  const [objectiveId, setObjectiveId] = useState<string>('');

  const formatTime = (iso: string) => {
    if (!iso) return '--';
    try { return new Date(iso).toUTCString().slice(0, 25) + 'Z'; }
    catch { return iso; }
  };

  const needsObjective = missionType !== 'intercept' && missionType !== 'area_patrol';

  const assignMission = () => {
    if (!unit || unit.destroyed) return;
    if (needsObjective && !objectiveId) return;
    onSend({
      type: 'assign_mission',
      unit_id: unit.id,
      mission_type: missionType,
      objective_id: needsObjective ? objectiveId : undefined,
      // For area patrol, patrol around the unit's current position
      patrol_lat: missionType === 'area_patrol' ? unit.lat : undefined,
      patrol_lon: missionType === 'area_patrol' ? unit.lon : undefined,
    });
  };

  const clearMission = () => {
    if (!unit) return;
    onSend({ type: 'clear_mission', unit_id: unit.id });
  };

  const hpPct = unit ? Math.max(0, (unit.hp / unit.max_hp) * 100) : 0;
  const hpColor = hpPct > 60 ? '#22cc66' : hpPct > 30 ? '#ddaa22' : '#cc3322';

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

          {/* HP bar */}
          {!unit.destroyed && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: '#4a6a8a', fontSize: 10, marginBottom: 2 }}>
                <span>INTEGRITY</span>
                <span style={{ color: hpColor }}>{Math.round(unit.hp)} / {Math.round(unit.max_hp)}</span>
              </div>
              <div style={{ background: '#0d1628', border: '1px solid #1e2e4a', height: 6 }}>
                <div style={{
                  width: `${hpPct}%`,
                  height: '100%',
                  background: hpColor,
                  transition: 'width 0.3s ease, background 0.3s ease',
                }} />
              </div>
            </div>
          )}

          <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 10 }}>
            <tbody>
              {([
                ['TYPE', `${unit.unit_class.toUpperCase()}${unit.unit_type ? ` / ${unit.unit_type}` : ''}`],
                ['POS', `${unit.lat.toFixed(3)}° ${unit.lon.toFixed(3)}°`],
                ['HDG', `${unit.heading.toFixed(0)}°`],
                ['SPD', `${unit.speed.toFixed(0)} km/h`],
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
              background: '#0d1628',
              border: '1px solid #1e2e4a',
              padding: '6px 8px',
              marginBottom: 10,
              fontSize: 11,
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
              </div>
              <div style={{ color: unit.mission.status === 'on_station' ? '#44dd77' : '#ddaa44', marginTop: 2 }}>
                {STATUS_LABELS[unit.mission.status]}
              </div>
            </div>
          ) : !unit.destroyed ? (
            <div style={{ color: '#3a5a7a', marginBottom: 10, fontSize: 11 }}>No active mission</div>
          ) : null}

          {!unit.destroyed && (
            <div style={{ borderTop: '1px solid #1e2e4a', paddingTop: 8 }}>
              <div style={{ color: '#4a6a8a', marginBottom: 6, fontSize: 10, letterSpacing: 1 }}>
                ASSIGN MISSION
              </div>

              <select
                value={missionType}
                onChange={(e) => setMissionType(e.target.value as MissionType)}
                style={selectStyle}
              >
                {(Object.keys(MISSION_LABELS) as MissionType[]).map((t) => (
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
