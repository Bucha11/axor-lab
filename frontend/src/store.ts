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
  // bearer token for publications-server WRITES (--write-token /
  // AXOR_LAB_WRITE_TOKEN): publish, attest, POST /api/incidents. Empty when
  // the server runs open (local dev).
  writeToken: string;
  // whose agent every experiment runs against, chosen once on the agent screen
  // instead of re-asked on every screen that starts a run
  agentSource: string;
  // the first-run wizard is done (or was skipped). Persisted, so the setup
  // questions are asked once and never again.
  setupDone: boolean;
  setLastRun: (runId: string) => void;
  setRuntimeRef: (runtimeRef: string | null) => void;
  setControlToken: (token: string) => void;
  setWriteToken: (token: string) => void;
  setAgentSource: (source: string) => void;
  setSetupDone: (done: boolean) => void;
}

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      lastRunId: null,
      runtimeRef: null,
      controlToken: "",
      writeToken: "",
      agentSource: "bundled",
      setupDone: false,
      setLastRun: (runId) => set({ lastRunId: runId }),
      setRuntimeRef: (runtimeRef) => set({ runtimeRef }),
      setControlToken: (token) => set({ controlToken: token }),
      setWriteToken: (token) => set({ writeToken: token }),
      setAgentSource: (agentSource) => set({ agentSource }),
      setSetupDone: (setupDone) => set({ setupDone }),
    }),
    { name: "axor-lab" },
  ),
);
