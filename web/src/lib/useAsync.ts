import { useCallback, useEffect, useState } from "react";

export interface Async<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
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
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    load()
      .then((value) => {
        if (live) setData(value);
      })
      .catch((exc: unknown) => {
        if (live) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, reload };
}
