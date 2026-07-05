import { useSimStore } from '../store/simStore';

const MONO = { fontFamily: '"Courier New", monospace' } as const;

const AMMO_LABEL: Record<string, string> = { aa: 'AIR-TO-AIR', ag: 'AIR-TO-GROUND', as: 'ANTI-SHIP' };
const AMMO_COLOR: Record<string, Record<string, string>> = {
  blue: { aa: '#00eeff', ag: '#ffdd00', as: '#ff8800' },
  red:  { aa: '#ff00cc', ag: '#ffdd00', as: '#ff3300' },
};

export function MissilePanel() {
  const missiles = useSimStore(s => s.missiles);
  const selectedMissileId = useSimStore(s => s.selectedMissileId);
  const selectMissile = useSimStore(s => s.selectMissile);
  const allUnits = useSimStore(s => s.units);

  const m = selectedMissileId ? missiles.find(x => x.id === selectedMissileId) : null;
  if (!m) return null;

  const target = allUnits.find(u => u.id === m.target_id);
  const accentColor = AMMO_COLOR[m.side]?.[m.ammo_type] ?? '#aaaaaa';
  const sideColor = m.side === 'blue' ? '#4488ff' : '#ff4444';

  const pct = m.total_ticks > 0
    ? Math.round(((m.total_ticks - m.ticks_remaining) / m.total_ticks) * 100)
    : 100;

  return (
    <div style={{
      position: 'absolute', top: 16, right: 16, zIndex: 10,
      width: 260,
      background: 'rgba(6, 10, 20, 0.92)',
      border: `1px solid ${accentColor}55`,
      ...MONO, fontSize: 11, color: '#8aa8c8',
      boxShadow: `0 0 18px ${accentColor}22`,
    }}>
      {/* Header */}
      <div style={{
        padding: '8px 12px 6px',
        borderBottom: `1px solid ${accentColor}44`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <span style={{ color: accentColor, letterSpacing: 1, fontSize: 12 }}>
            ⬡ {AMMO_LABEL[m.ammo_type] ?? m.ammo_type.toUpperCase()}
          </span>
          <span style={{
            marginLeft: 8, fontSize: 10, letterSpacing: 1,
            color: sideColor,
          }}>
            {m.side.toUpperCase()}
          </span>
        </div>
        <button
          onClick={() => selectMissile(null)}
          style={{
            background: 'none', border: 'none', color: '#4a6a8a',
            cursor: 'pointer', fontSize: 13, lineHeight: 1, padding: 0,
          }}
        >✕</button>
      </div>

      {/* Progress bar */}
      <div style={{ height: 2, background: '#0a1428' }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: accentColor, opacity: 0.7,
          transition: 'width 0.3s ease',
        }} />
      </div>

      {/* Stats */}
      <div style={{ padding: '8px 12px 10px' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            <Row label="FIRER" value={m.firer_name} color='#aaccee' />
            <Row label="TARGET" value={target ? target.name : m.target_name || m.target_id} color='#aaccee' />
            <Row label="ALTITUDE" value={`${m.altitude_m.toFixed(0)} m`} />
            <Row label="SPEED" value={`${m.speed_kmh.toFixed(0)} km/h`} />
            <Row label="HEADING" value={`${m.heading.toFixed(0)}°`} />
            <Row label="RCS" value={`${m.rcs < 0.01 ? m.rcs.toExponential(2) : m.rcs.toFixed(3)} m²`} />
            <Row label="IMPACT IN" value={`${m.ticks_remaining} tick${m.ticks_remaining !== 1 ? 's' : ''}`} color={m.ticks_remaining <= 2 ? '#ff4444' : undefined} />
          </tbody>
        </table>

        {/* Detection physics note */}
        <div style={{
          marginTop: 8, padding: '5px 8px',
          background: '#0a1428', border: '1px solid #1a2a3a',
          fontSize: 10, color: '#4a6a7a', lineHeight: 1.5,
        }}>
          {m.ammo_type === 'as'
            ? `Sea-skimming at ${m.altitude_m.toFixed(0)} m — horizon-limited detection`
            : m.ammo_type === 'aa'
            ? `High-altitude intercept at ${m.altitude_m.toFixed(0)} m`
            : `Low-level strike at ${m.altitude_m.toFixed(0)} m`}
          {m.waypoints && m.waypoints.length > 0
            ? ` · routing around land`
            : ''}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <tr>
      <td style={{ color: '#4a6a8a', paddingBottom: 3, paddingRight: 10, whiteSpace: 'nowrap' }}>
        {label}
      </td>
      <td style={{ color: color ?? '#8aa8c8', textAlign: 'right' }}>{value}</td>
    </tr>
  );
}
