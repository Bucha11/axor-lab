// Tiny hash router — deep links matter (share a run at #/runs/{run_id}, a
// publication at #/e/{publication_id}, an EvidenceCase at
// #/e/{publication_id}/evidence/{trace_id}). No external dependency: the URL
// hash is the single source of truth, and useRoute subscribes to it.
import { useEffect, useState } from "react";

export interface Route {
  // path segments after '#/', e.g. ["runs", "run_0001_ab12"]
  segments: string[];
  // query params after '?', e.g. { policy: "strict" }
  query: Record<string, string>;
  raw: string;
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const segments = pathPart ? pathPart.split("/").filter(Boolean) : [];
  const query: Record<string, string> = {};
  if (queryPart) {
    for (const pair of queryPart.split("&")) {
      const [k, v] = pair.split("=");
      if (k) query[decodeURIComponent(k)] = decodeURIComponent(v ?? "");
    }
  }
  return { segments, query, raw };
}

export function navigate(
  path: string,
  query?: Record<string, string | number | undefined>,
): void {
  let hash = `#/${path.replace(/^\//, "")}`;
  if (query) {
    const parts = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    if (parts.length) hash += `?${parts.join("&")}`;
  }
  window.location.hash = hash;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = (): void => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
