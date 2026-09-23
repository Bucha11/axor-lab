import { useCallback, useEffect, useRef, useState } from "react";
import { currentToken } from "./api";

export interface RunProgress {
  state: string;
  terminal: boolean;
  /** trials that FINISHED — the server's `trials` frame counts completed AND
   * failed together (runtime_jobs `_frames_locked`). It is not the report's
   * `coverage.completed`, which counts completed only; a screen must label it
   * as finished, never as completed. */
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
 *
 * `ended` says the stream stopped WITHOUT the run reaching a terminal state:
 * the server's `timeout` frame (it caps a stream at its max_seconds), or an
 * HTTP error the browser will not retry (EventSource closes itself on a
 * non-200). Either way `progress` is frozen at its last frame and nothing will
 * update it — a screen that kept painting it as "live" would be lying about a
 * run nobody is watching any more. `reconnect` opens a fresh stream.
 */
export type StreamEnd = "timeout" | "error";

export interface RunEvents {
  progress: RunProgress | null;
  ended: StreamEnd | null;
  reconnect: () => void;
}

export function useRunEvents(runId: string, enabled = true): RunEvents {
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [ended, setEnded] = useState<StreamEnd | null>(null);
  const [nonce, setNonce] = useState(0);
  const done = useRef(false);
  const reconnect = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    // a new run (or a reconnect) starts from nothing: the previous run's
    // progress would otherwise paint over this one until its first frame
    setProgress(null);
    setEnded(null);
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
      // re-opens a fresh server-side stream each time. It IS reported, though:
      // the last frame is now stale.
      done.current = true;
      source.close();
      setEnded("timeout");
    });
    source.onerror = () => {
      // a closed stream after `done`/`timeout` is the normal end, not a failure
      if (done.current) {
        source.close();
        return;
      }
      // CONNECTING means the browser is retrying a dropped connection itself
      // (and the server re-sends the snapshot on reconnect). CLOSED means it
      // gave up — an HTTP error — and no further frame will ever arrive.
      if (source.readyState === EventSource.CLOSED) setEnded("error");
    };
    return () => source.close();
  }, [runId, enabled, nonce]);

  return { progress, ended, reconnect };
}

