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
  if (e.type === 'captured') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `◆ ${e.objective_name} CAPTURED by ${side.toUpperCase()} (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'out_of_ammo') {
    const cat = e.ammo_type === 'aa' ? 'A-A' : e.ammo_type === 'ag' ? 'A-G' : e.ammo_type === 'as' ? 'A-S' : (e.ammo_type ?? '?');
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `▣ ${e.unit_name} [${side.toUpperCase()}] OUT OF ${cat} AMMO (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'low_fuel') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `⚠ ${e.unit_name} [${side.toUpperCase()}] LOW FUEL (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'rtb_complete') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `↩ ${e.unit_name} [${side.toUpperCase()}] REARMED & REFUELLED (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'bingo_fuel') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `⛽ ${e.unit_name} [${side.toUpperCase()}] BINGO FUEL — RTB (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'winchester') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `▣ ${e.unit_name} [${side.toUpperCase()}] WINCHESTER — RTB (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'commander_assign') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    return {
      text: `⊞ CMD [${side.toUpperCase()}] → ${e.unit_name}: ${(e.mission ?? '').toUpperCase()} ${e.objective ?? ''} (T+${e.tick ?? '?'})`,
      color,
    };
  }
  if (e.type === 'missile_intercept') {
    const side = e.side ?? 'unknown';
    const color = side in SIDE_COLOR ? SIDE_COLOR[side as 'blue' | 'red'] : '#aaa';
    const cat = e.missile_type === 'aa' ? 'AAM' : e.missile_type === 'ag' ? 'ALCM' : e.missile_type === 'as' ? 'ASM' : (e.missile_type ?? 'missile');
    return {
      text: `◎ ${e.interceptor_name} [${side.toUpperCase()}] INTERCEPTS ${cat} from ${e.firer_name ?? '?'} (T+${e.tick ?? '?'})`,
      color,
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
