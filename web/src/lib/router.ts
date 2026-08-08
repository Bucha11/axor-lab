import { useEffect, useState } from "react";

/**
 * Hash routing, deliberately.
 *
 * The app is served by a stdlib `http.server` that knows nothing about SPA
 * fallbacks; a path router would 404 on every reload. A dependency-free hash
 * router keeps every screen linkable, which is what the contract's screen table
 * assumes.
 */
export function useRoute(): string {
  const [route, setRoute] = useState(() => window.location.hash.slice(1) || "/");
  useEffect(() => {
    const onChange = () => setRoute(window.location.hash.slice(1) || "/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function navigate(route: string): void {
  window.location.hash = route;
}

/** `/runs/:id/trials/:tid` -> ["runs", id, "trials", tid] */
export function segments(route: string): string[] {
  return route.split("/").filter(Boolean).map(decodeURIComponent);
}
