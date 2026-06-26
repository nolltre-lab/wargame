import type { RingToggles } from '../types';

interface Props {
  rings: RingToggles;
  onChange: (rings: RingToggles) => void;
}

const BUTTONS: { key: keyof RingToggles; label: string; color: string }[] = [
  { key: 'sensor',       label: 'SENSOR',  color: '#4488ff' },
  { key: 'airWeapon',    label: 'A-A/S-A', color: '#ff8800' },
  { key: 'surfaceWeapon',label: 'A-S/S-S', color: '#ff3300' },
];

export function RangeControls({ rings, onChange }: Props) {
  const toggle = (key: keyof RingToggles) =>
    onChange({ ...rings, [key]: !rings[key] });

  return (
    <div style={{
      position: 'absolute', bottom: 24, left: '50%',
      transform: 'translateX(calc(-50% + 120px))',
      zIndex: 10,
      display: 'flex', gap: 6,
      background: 'rgba(8, 12, 22, 0.88)',
      border: '1px solid #1e2e4a',
      padding: '6px 12px',
      fontFamily: '"Courier New", monospace',
      fontSize: 11,
    }}>
      <span style={{ color: '#4a6a8a', letterSpacing: 1, alignSelf: 'center', marginRight: 4 }}>RANGES</span>
      {BUTTONS.map(({ key, label, color }) => {
        const active = rings[key];
        return (
          <button
            key={key}
            onClick={() => toggle(key)}
            style={{
              background: active ? `${color}22` : 'transparent',
              border: `1px solid ${active ? color : '#2a3e5a'}`,
              color: active ? color : '#4a6a8a',
              fontFamily: '"Courier New", monospace',
              fontSize: 11,
              padding: '4px 10px',
              cursor: 'pointer',
              letterSpacing: 1,
              transition: 'all 0.15s',
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
