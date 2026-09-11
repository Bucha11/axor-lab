import { useEffect, useRef, useState } from "react";
import { currentToken } from "./api";

export interface RunProgress {
  state: string;
  terminal: boolean;
  completed: number;
  planned: number;
  trials: { trial_id: string; status: string; metrics?: Record<string, unknown> }[];
}

/**
 * Live run progress over `SSE /runs/{id}/events`.
 *
 * `EventSource` cannot send an Authorization header, and the stream is gated by
 * the control token like every other screen endpoint — so the token goes in the
 * query string for this ONE endpoint. That is a real trade: a token in a URL can
 * land in a proxy log. It is scoped to a local control token the operator passes
 * to their own server, and the alternative — an unauthenticated stream — leaks
 * run contents to anyone who can reach the port.
 *
 * The server closes the stream when the run reaches a terminal state, and sends
 * `done` first. Without acting on that, the browser would reconnect forever to
 * a run that will never move again.
 */
export function useRunEvents(runId: string, enabled = true): RunProgress | null {
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const done = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    done.current = false;
    const token = currentToken();
    const url = `/runs/${encodeURIComponent(runId)}/events${
      token ? `?token=${encodeURIComponent(token)}` : ""
    }`;
    const source = new EventSource(url);

    source.addEventListener("state", (event) => {
      const payload = JSON.parse((event as MessageEvent).data);
      setProgress((prior) => ({
        state: String(payload.state),
        terminal: Boolean(payload.terminal),
        completed: prior?.completed ?? 0,
        planned: prior?.planned ?? 0,
        trials: prior?.trials ?? [],
      }));
    });
    source.addEventListener("trials", (event) => {
      const payload = JSON.parse((event as MessageEvent).data);
      setProgress((prior) => ({
        state: prior?.state ?? "",
        terminal: prior?.terminal ?? false,
        completed: Number(payload.completed ?? 0),
        planned: Number(payload.planned ?? 0),
        trials: payload.trials ?? [],
      }));
    });
    source.addEventListener("done", () => {
      done.current = true;
      source.close();
    });
    source.addEventListener("timeout", () => {
      // the server caps a live stream and closes it with a `timeout` frame for
      // a run that never reached terminal (a stuck runtime). Treat it as an end,
      // not an error — otherwise EventSource silently reconnects forever and
      // re-opens a fresh server-side stream each time.
      done.current = true;
      source.close();
    });
    source.onerror = () => {
      // a closed stream after `done`/`timeout` is the normal end, not a failure
      if (done.current) source.close();
    };
    return () => source.close();
  }, [runId, enabled]);

  return progress;
}
