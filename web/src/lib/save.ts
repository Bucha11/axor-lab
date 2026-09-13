/**
 * Hand the browser a file.
 *
 * Lived inside the Handoff screen, where it was the ONLY download in the whole
 * app — the hosted face could look at evidence and not give it to anyone. It is
 * shared now because every export surface needs the same three lines, and a
 * second copy is how one of them ends up leaking its object URL.
 */
export function saveFile(name: string, body: string, type = "application/json"): void {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** The same, for bytes the server already framed (a zip). */
export function saveBlob(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}
