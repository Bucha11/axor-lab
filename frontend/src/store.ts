// Client/UI state (Zustand for UI state, TanStack Query for server state).
// Deliberately small: the session focus (last run), the runtime the builder
// assigns to, and the runtime-jobs control token. Everything server-derived
// stays in Query.
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AppState {
  // last run started from the builder — the runs/results tabs default to it
  lastRunId: string | null;
  // the runtime the builder targets (a runtime_ref from POST /runtimes/connect)
  runtimeRef: string | null;
  // bearer token for the runtime-jobs CONTROL surface (--control-token /
  // AXOR_LAB_CONTROL_TOKEN on the server). Empty when the server runs open.
  controlToken: string;
  setLastRun: (runId: string) => void;
  setRuntimeRef: (runtimeRef: string | null) => void;
  setControlToken: (token: string) => void;
}

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      lastRunId: null,
      runtimeRef: null,
      controlToken: "",
      setLastRun: (runId) => set({ lastRunId: runId }),
      setRuntimeRef: (runtimeRef) => set({ runtimeRef }),
      setControlToken: (token) => set({ controlToken: token }),
    }),
    { name: "axor-lab" },
  ),
);
