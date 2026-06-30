import { useState } from 'react';
import { useSimStore } from '../store/simStore';
import type { SideGoal, Side } from '../types';

const API = 'http://localhost:8000';
const MONO = { fontFamily: '"Courier New", monospace' } as const;

const GOAL_TYPES: SideGoal['type'][] = ['hold', 'capture', 'intercept', 'patrol', 'strike'];

const GOAL_LABEL: Record<SideGoal['type'], string> = {
  hold:      'HOLD',
  capture:   'CAPTURE',
  intercept: 'INTERCEPT',
  patrol:    'PATROL',
  strike:    'STRIKE',
};

const GOAL_COLOR: Record<SideGoal['type'], string> = {
  hold:      '#22cc88',
  capture:   '#ffaa22',
  intercept: '#ff4488',
  patrol:    '#4488ff',
  strike:    '#ff6633',
};

const SIDE_COLOR: Record<Side, string> = {
  blue: '#4488ff',
  red:  '#ff4444',
};

async function postGoals(side: Side, goals: SideGoal[]) {
  await fetch(`${API}/goals/${side}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goals }),
  });
}

export function GoalsPanel() {
  const goals    = useSimStore((s) => s.goals);
  const setGoals = useSimStore((s) => s.setGoals);
  const objectives = useSimStore((s) => s.objectives);

  const [activeSide, setActiveSide] = useState<Side>('blue');
  const [addType, setAddType] = useState<SideGoal['type']>('hold');
  const [addObjId, setAddObjId] = useState<string>('');

  const sideGoals = goals[activeSide] ?? [];

  const needsObjective = (t: SideGoal['type']) => t === 'hold' || t === 'capture' || t === 'patrol';

  const addGoal = async () => {
    const obj_id = needsObjective(addType) ? (addObjId || null) : null;
    const newGoal: SideGoal = {
      type: addType,
      priority: sideGoals.length + 1,
      objective_id: obj_id,
      area_lat: null,
      area_lon: null,
      ground_count: 0,
      air_count: 0,
      naval_count: 0,
    };
    const updated = [...sideGoals, newGoal];
    setGoals(activeSide, updated);
    await postGoals(activeSide, updated);
    setAddObjId('');
  };

  const removeGoal = async (idx: number) => {
    const updated = sideGoals
      .filter((_, i) => i !== idx)
      .map((g, i) => ({ ...g, priority: i + 1 }));
    setGoals(activeSide, updated);
    await postGoals(activeSide, updated);
  };

  const moveUp = async (idx: number) => {
    if (idx === 0) return;
    const updated = [...sideGoals];
    [updated[idx - 1], updated[idx]] = [updated[idx], updated[idx - 1]];
    const reprioritized = updated.map((g, i) => ({ ...g, priority: i + 1 }));
    setGoals(activeSide, reprioritized);
    await postGoals(activeSide, reprioritized);
  };

  const objName = (id: string | null) => {
    if (!id) return '';
    return objectives.find((o) => o.id === id)?.name ?? id;
  };

  return (
    <div style={{
      position: 'absolute', top: 16, left: 320, width: 320, zIndex: 10,
      background: 'rgba(8,12,22,0.92)', border: '1px solid #1e2e4a',
      ...MONO, fontSize: 12, color: '#8aaac8',
    }}>
      {/* Header */}
      <div style={{ padding: '8px 12px', borderBottom: '1px solid #1e2e4a', color: '#c8ddf0', letterSpacing: 1, fontSize: 11 }}>
        COMMANDER GOALS
      </div>

      {/* Side tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid #1e2e4a' }}>
        {(['blue', 'red'] as Side[]).map((side) => (
          <button key={side} onClick={() => setActiveSide(side)} style={{
            flex: 1, padding: '6px 0', background: activeSide === side ? `${SIDE_COLOR[side]}18` : 'transparent',
            border: 'none', borderBottom: activeSide === side ? `2px solid ${SIDE_COLOR[side]}` : '2px solid transparent',
            color: activeSide === side ? SIDE_COLOR[side] : '#4a6a8a',
            ...MONO, fontSize: 11, cursor: 'pointer', letterSpacing: 1,
          }}>
            {side.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Goal list */}
      <div style={{ minHeight: 60 }}>
        {sideGoals.length === 0 && (
          <div style={{ padding: '10px 12px', color: '#3a5a7a', fontSize: 11 }}>
            No goals assigned — units will orbit or hold position.
          </div>
        )}
        {sideGoals.map((g, i) => (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '5px 10px', borderBottom: '1px solid #111e30',
          }}>
            <span style={{ color: '#4a6a8a', minWidth: 18, fontSize: 10 }}>P{g.priority}</span>
            <span style={{
              color: GOAL_COLOR[g.type], minWidth: 70, fontSize: 11, letterSpacing: 1,
            }}>
              {GOAL_LABEL[g.type]}
            </span>
            <span style={{ flex: 1, color: '#8aaac8', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {g.objective_id ? objName(g.objective_id) : ''}
            </span>
            <div style={{ display: 'flex', gap: 3 }}>
              <button onClick={() => moveUp(i)} style={{
                background: 'transparent', border: '1px solid #1e2e4a', color: '#4a6a8a',
                ...MONO, fontSize: 10, padding: '1px 5px', cursor: i === 0 ? 'default' : 'pointer',
                opacity: i === 0 ? 0.3 : 1,
              }}>↑</button>
              <button onClick={() => removeGoal(i)} style={{
                background: 'transparent', border: '1px solid #3a1e1e', color: '#884444',
                ...MONO, fontSize: 10, padding: '1px 5px', cursor: 'pointer',
              }}>×</button>
            </div>
          </div>
        ))}
      </div>

      {/* Add goal row */}
      <div style={{ padding: '8px 10px', borderTop: '1px solid #1e2e4a', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <select
          value={addType}
          onChange={(e) => setAddType(e.target.value as SideGoal['type'])}
          style={{ background: '#0a1020', border: '1px solid #1e3050', color: '#8aaac8', ...MONO, fontSize: 11, padding: '3px 6px', cursor: 'pointer' }}
        >
          {GOAL_TYPES.map((t) => (
            <option key={t} value={t}>{GOAL_LABEL[t]}</option>
          ))}
        </select>

        {needsObjective(addType) && (
          <select
            value={addObjId}
            onChange={(e) => setAddObjId(e.target.value)}
            style={{ flex: 1, background: '#0a1020', border: '1px solid #1e3050', color: '#8aaac8', ...MONO, fontSize: 11, padding: '3px 6px', cursor: 'pointer', minWidth: 0 }}
          >
            <option value="">— objective —</option>
            {objectives.map((o) => (
              <option key={o.id} value={o.id}>{o.name}</option>
            ))}
          </select>
        )}

        <button
          onClick={addGoal}
          disabled={needsObjective(addType) && !addObjId}
          style={{
            background: '#002a1a', border: '1px solid #226644', color: '#44cc88',
            ...MONO, fontSize: 11, padding: '3px 10px', cursor: 'pointer', letterSpacing: 1,
            opacity: needsObjective(addType) && !addObjId ? 0.4 : 1,
          }}>
          + ADD
        </button>
      </div>
    </div>
  );
}
