import { useState } from 'react';
import { MapView } from './components/MapView';
import { UnitPanel } from './components/UnitPanel';
import { EventLog } from './components/EventLog';
import { GoalsPanel } from './components/GoalsPanel';
import { ScenarioBuilder } from './components/ScenarioBuilder';
import { useSimSocket } from './hooks/useSimSocket';
import { useSimStore, type Perspective } from './store/simStore';
import type { RingToggles } from './types';

const API = 'http://localhost:8000';
const MONO = { fontFamily: '"Courier New", monospace' } as const;

export default function App() {
  const { send } = useSimSocket();
  const running = useSimStore((s) => s.running);
  const perspective = useSimStore((s) => s.perspective);
  const setPerspective = useSimStore((s) => s.setPerspective);
  const [rings, setRings] = useState<RingToggles>({ sensor: false, airWeapon: false, surfaceWeapon: false });
  const [mode, setMode] = useState<'sim' | 'builder'>('sim');
  const [showGoals, setShowGoals] = useState(false);

  const toggleSim = async () => {
    const endpoint = running ? '/sim/pause' : '/sim/start';
    await fetch(`${API}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speed: 60 }),
    });
  };

  if (mode === 'builder') {
    return (
      <div style={{ width: '100vw', height: '100vh', position: 'relative' }}>
        <ScenarioBuilder onExit={() => setMode('sim')} />
      </div>
    );
  }

  return (
    <div style={{ width: '100vw', height: '100vh', position: 'relative' }}>
      <MapView rings={rings} />
      <UnitPanel onSend={send} />
      <EventLog />
      {showGoals && <GoalsPanel />}

      {/* Sim control bar */}
      <div style={{
        position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
        zIndex: 10, display: 'flex', gap: 8, alignItems: 'center',
        background: 'rgba(8, 12, 22, 0.88)', border: '1px solid #1e2e4a',
        padding: '8px 16px', ...MONO, fontSize: 12, color: '#4a6a8a',
      }}>
        <span style={{ marginRight: 8, letterSpacing: 1 }}>SIM CONTROL</span>
        <button onClick={toggleSim} style={{
          background: running ? '#1a0a00' : '#003318',
          border: `1px solid ${running ? '#cc4422' : '#22cc66'}`,
          color: running ? '#cc4422' : '#22cc66',
          ...MONO, fontSize: 12, padding: '5px 18px', cursor: 'pointer', letterSpacing: 1,
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
          <button key={key} onClick={() => setRings(r => ({ ...r, [key]: !r[key] }))} style={{
            background: rings[key] ? `${color}22` : 'transparent',
            border: `1px solid ${rings[key] ? color : '#2a3e5a'}`,
            color: rings[key] ? color : '#4a6a8a',
            ...MONO, fontSize: 11, padding: '4px 10px', cursor: 'pointer', letterSpacing: 1,
          }}>
            {label}
          </button>
        ))}

        <div style={{ width: 1, height: 20, background: '#1e2e4a', margin: '0 4px' }} />

        <span style={{ letterSpacing: 1 }}>VIEW</span>
        {([
          { key: 'god'  as Perspective, label: '⊕ GOD',  color: '#aaaaaa' },
          { key: 'blue' as Perspective, label: '◑ BLUE', color: '#4488ff' },
          { key: 'red'  as Perspective, label: '◑ RED',  color: '#ff4444' },
        ]).map(({ key, label, color }) => (
          <button key={key} onClick={() => setPerspective(key)} style={{
            background: perspective === key ? `${color}22` : 'transparent',
            border: `1px solid ${perspective === key ? color : '#2a3e5a'}`,
            color: perspective === key ? color : '#4a6a8a',
            ...MONO, fontSize: 11, padding: '4px 10px', cursor: 'pointer', letterSpacing: 1,
          }}>
            {label}
          </button>
        ))}

        <div style={{ width: 1, height: 20, background: '#1e2e4a', margin: '0 4px' }} />

        <button onClick={() => setShowGoals(g => !g)} style={{
          background: showGoals ? '#00220a' : 'transparent',
          border: `1px solid ${showGoals ? '#22cc66' : '#2a4a6a'}`,
          color: showGoals ? '#22cc66' : '#4a8aaa',
          ...MONO, fontSize: 11, padding: '4px 12px', cursor: 'pointer', letterSpacing: 1,
        }}>
          ⊞ GOALS
        </button>

        <button onClick={() => setMode('builder')} style={{
          background: 'transparent', border: '1px solid #2a4a6a',
          color: '#4a8aaa', ...MONO, fontSize: 11, padding: '4px 12px',
          cursor: 'pointer', letterSpacing: 1,
        }}>
          ✎ BUILD
        </button>
      </div>
    </div>
  );
}
