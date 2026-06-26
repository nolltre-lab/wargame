import { create } from 'zustand';
import type { SimState, Unit, Objective, CombatEvent } from '../types';

const MAX_LOG_EVENTS = 50;

interface SimStore extends Omit<SimState, 'events'> {
  latestEvents: CombatEvent[];
  eventLog: CombatEvent[];
  selectedUnitId: string | null;
  selectUnit: (id: string | null) => void;
  setSimState: (state: SimState) => void;
  getSelectedUnit: () => Unit | null;
  getObjective: (id: string) => Objective | undefined;
}

export const useSimStore = create<SimStore>((set, get) => ({
  sim_time: '',
  tick: 0,
  running: false,
  units: [],
  objectives: [],
  latestEvents: [],
  eventLog: [],
  selectedUnitId: null,

  selectUnit: (id) => set({ selectedUnitId: id }),

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
        latestEvents: incoming,
        eventLog: newLog,
      };
    }),

  getSelectedUnit: () => {
    const { units, selectedUnitId } = get();
    return units.find((u) => u.id === selectedUnitId) ?? null;
  },

  getObjective: (id) => get().objectives.find((o) => o.id === id),
}));
