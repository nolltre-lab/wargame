import { useSimStore } from '../store/simStore';
import type { CombatEvent } from '../types';

const SIDE_COLOR = { blue: '#4488ff', red: '#ff4444' };

function formatEvent(e: CombatEvent): { text: string; color: string } {
  if (e.type === 'destroyed') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `✕ ${e.unit_name} [${side.toUpperCase()}] DESTROYED (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'engagement') {
    const hp = e.target_hp ?? 0;
    const max = e.target_max_hp ?? 1;
    const pct = Math.round((hp / max) * 100);
    return {
      text: `⚡ ${e.attacker_name} → ${e.target_name}  [${pct}% HP]`,
      color: '#88aacc',
    };
  }
  return { text: JSON.stringify(e), color: '#666' };
}

export function EventLog() {
  const eventLog = useSimStore((s) => s.eventLog);
  if (eventLog.length === 0) return null;

  return (
    <div style={{
      position: 'absolute',
      bottom: 72,
      right: 16,
      zIndex: 10,
      width: 340,
      maxHeight: 220,
      overflowY: 'auto',
      background: 'rgba(8, 12, 22, 0.88)',
      border: '1px solid #1e2e4a',
      fontFamily: '"Courier New", monospace',
      fontSize: 11,
      color: '#c8d8f0',
    }}>
      <div style={{
        padding: '5px 10px',
        borderBottom: '1px solid #1e2e4a',
        color: '#4a6a8a',
        letterSpacing: 1,
        fontSize: 10,
      }}>
        COMBAT LOG
      </div>
      {eventLog.map((e, i) => {
        const { text, color } = formatEvent(e);
        return (
          <div
            key={i}
            style={{
              padding: '3px 10px',
              color,
              borderBottom: '1px solid #0d1628',
              opacity: i === 0 ? 1 : Math.max(0.3, 1 - i * 0.04),
            }}
          >
            {text}
          </div>
        );
      })}
    </div>
  );
}
