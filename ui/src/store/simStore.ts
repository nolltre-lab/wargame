import { create } from 'zustand';
import type { SimState, Unit, Objective, CombatEvent, SideGoal } from '../types';

const MAX_LOG_EVENTS = 50;

export type Perspective = 'god' | 'blue' | 'red';

interface SimStore extends Omit<SimState, 'events'> {
  latestEvents: CombatEvent[];
  eventLog: CombatEvent[];
  selectedUnitId: string | null;
  perspective: Perspective;
  selectUnit: (id: string | null) => void;
  setPerspective: (p: Perspective) => void;
  setSimState: (state: SimState) => void;
  getSelectedUnit: () => Unit | null;
  getObjective: (id: string) => Objective | undefined;
  setGoals: (side: 'blue' | 'red', goals: SideGoal[]) => void;
}

export const useSimStore = create<SimStore>((set, get) => ({
  sim_time: '',
  tick: 0,
  running: false,
  units: [],
  objectives: [],
  blue_detected: [],
  red_detected: [],
  goals: { blue: [], red: [] },
  latestEvents: [],
  eventLog: [],
  selectedUnitId: null,
  perspective: 'god',

  selectUnit: (id) => set({ selectedUnitId: id }),
  setPerspective: (p) => set({ perspective: p }),

  setSimState: (state) =>
    set((prev) => {
      const incoming = state.events ?? [];
      const newLog = incoming.length > 0
        ? [...incoming, ...prev.eventLog].slice(0, MAX_LOG_EVENTS)
        : prev.eventLog;
      return {
        sim_time: state.sim_time,
        tick: state.tick,
        running: state.running,
        units: state.units,
        objectives: state.objectives,
        blue_detected: state.blue_detected ?? [],
        red_detected: state.red_detected ?? [],
        goals: state.goals ?? prev.goals,
        latestEvents: incoming,
        eventLog: newLog,
      };
    }),

  getSelectedUnit: () => {
    const { units, selectedUnitId } = get();
    return units.find((u) => u.id === selectedUnitId) ?? null;
  },

  getObjective: (id) => get().objectives.find((o) => o.id === id),

  setGoals: (side, goals) =>
    set((prev) => ({ goals: { ...prev.goals, [side]: goals } })),
}));
