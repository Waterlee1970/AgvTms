/**
 * Global state management using Zustand.
 */

import { create } from 'zustand';
import type {
  AgvStatus,
  AgvTask,
  AlgorithmConfig,
  ScheduleResult,
  MapNode,
  MapEdge,
  ConveyorSegment,
} from '../services/api';

interface AppState {
  // AGV
  agvs: AgvStatus[];
  setAgvs: (agvs: AgvStatus[]) => void;

  // Tasks
  tasks: AgvTask[];
  setTasks: (tasks: AgvTask[]) => void;

  // Map
  mapNodes: MapNode[];
  mapEdges: MapEdge[];
  setMapNodes: (nodes: MapNode[]) => void;
  setMapEdges: (edges: MapEdge[]) => void;

  // Conveyor
  conveyorSegments: ConveyorSegment[];
  setConveyorSegments: (segments: ConveyorSegment[]) => void;

  // Schedule
  scheduleResult: ScheduleResult | null;
  setScheduleResult: (result: ScheduleResult | null) => void;

  // Algorithm config
  algorithmConfig: AlgorithmConfig | null;
  setAlgorithmConfig: (config: AlgorithmConfig) => void;

  // Loading states
  loading: Record<string, boolean>;
  setLoading: (key: string, value: boolean) => void;

  // Metrics
  metrics: Record<string, unknown> | null;
  setMetrics: (metrics: Record<string, unknown>) => void;
}

export const useStore = create<AppState>((set) => ({
  agvs: [],
  setAgvs: (agvs) => set({ agvs }),

  tasks: [],
  setTasks: (tasks) => set({ tasks }),

  mapNodes: [],
  mapEdges: [],
  setMapNodes: (mapNodes) => set({ mapNodes }),
  setMapEdges: (mapEdges) => set({ mapEdges }),

  conveyorSegments: [],
  setConveyorSegments: (conveyorSegments) => set({ conveyorSegments }),

  scheduleResult: null,
  setScheduleResult: (scheduleResult) => set({ scheduleResult }),

  algorithmConfig: null,
  setAlgorithmConfig: (algorithmConfig) => set({ algorithmConfig }),

  loading: {},
  setLoading: (key, value) =>
    set((state) => ({ loading: { ...state.loading, [key]: value } })),

  metrics: null,
  setMetrics: (metrics) => set({ metrics }),
}));
