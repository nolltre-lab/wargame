import { useState } from 'react';
import { MapView } from './components/MapView';
import { UnitPanel } from './components/UnitPanel';
import { EventLog } from './components/EventLog';
import { useSimSocket } from './hooks/useSimSocket';
import { useSimStore } from './store/simStore';
import type { RingToggles } from './types';

const API = 'http://localhost:8000';

export default function App() {
  const { send } = useSimSocket();
  const running = useSimStore((s) => s.running);
  const [rings, setRings] = useState<RingToggles>({ sensor: false, airWeapon: false, surfaceWeapon: false });

  const toggleSim = async () => {
    const endpoint = running ? '/sim/pause' : '/sim/start';
    await fetch(`${API}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speed: 60 }),
    });
  };

  return (
    <div style={{ width: '100vw', height: '100vh', position: 'relative' }}>
      <MapView rings={rings} />
      <UnitPanel onSend={send} />
      <EventLog />

      {/* Sim control + range toggles */}
      <div style={{
        position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
        zIndex: 10, display: 'flex', gap: 8, alignItems: 'center',
        background: 'rgba(8, 12, 22, 0.88)',
        border: '1px solid #1e2e4a',
        padding: '8px 16px',
        fontFamily: '"Courier New", monospace',
        fontSize: 12,
        color: '#4a6a8a',
      }}>
        <span style={{ marginRight: 8, letterSpacing: 1 }}>SIM CONTROL</span>
        <button onClick={toggleSim} style={{
          background: running ? '#1a0a00' : '#003318',
          border: `1px solid ${running ? '#cc4422' : '#22cc66'}`,
          color: running ? '#cc4422' : '#22cc66',
          fontFamily: '"Courier New", monospace',
          fontSize: 12,
          padding: '5px 18px',
          cursor: 'pointer',
          letterSpacing: 1,
        }}>
          {running ? '■  PAUSE' : '▶  RUN  60×'}
        </button>

        <div style={{ width: 1, height: 20, background: '#1e2e4a', margin: '0 4px' }} />

        <span style={{ letterSpacing: 1 }}>RANGES</span>
        {([
          { key: 'sensor'        as const, label: 'SENSOR',  color: '#4488ff' },
          { key: 'airWeapon'     as const, label: 'A-A/S-A', color: '#ff8800' },
          { key: 'surfaceWeapon' as const, label: 'A-S/S-S', color: '#ff3300' },
        ]).map(({ key, label, color }) => (
          <button
            key={key}
            onClick={() => setRings(r => ({ ...r, [key]: !r[key] }))}
            style={{
              background: rings[key] ? `${color}22` : 'transparent',
              border: `1px solid ${rings[key] ? color : '#2a3e5a'}`,
              color: rings[key] ? color : '#4a6a8a',
              fontFamily: '"Courier New", monospace',
              fontSize: 11,
              padding: '4px 10px',
              cursor: 'pointer',
              letterSpacing: 1,
            }}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
