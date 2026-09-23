import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

export interface Async<T> {
  data: T | null;
  error: string | null;
  /** the HTTP status behind `error`, when there was one.
   *
   * 402 is the status this exists for: "your plan does not include this" is the
   * one failure a user can FIX, and flattening it to a red sentence like any
   * other left the whole billing system with nowhere to act on it. */
  status: number | null;
  loading: boolean;
  reload: () => void;
}

/** A caught exception as the message + HTTP status a screen renders — the same
 * split `useAsync` makes, for a button's request (confirm, cancel, delete)
 * that is not a load. Keeping the status is what lets a 402 still say "your
 * plan", and a 409 read as a refusal rather than a crash. */
export function describeError(exc: unknown): { message: string; status: number | null } {
  return {
    message: exc instanceof Error ? exc.message : String(exc),
    status: exc instanceof ApiError ? exc.status : null,
  };
}

/**
 * Load once, expose the three states a screen has to render.
 *
 * `error` is a first-class state, not a silent empty result. A screen that
 * renders an empty list on a failed request tells the user their workspace is
 * empty, which is a different and much worse lie than "this did not load".
 */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    setStatus(null);
    load()
      .then((value) => {
        if (live) setData(value);
      })
      .catch((exc: unknown) => {
        if (!live) return;
        const { message, status: code } = describeError(exc);
        setError(message);
        setStatus(code);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, status, loading, reload };
}
