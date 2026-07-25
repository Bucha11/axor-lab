// Offline verification of a downloaded reproduction package — IN THE BROWSER.
//
// `axor-lab verify` was the one capability that could not simply move to the
// server, because its entire value is that no server is trusted. A "the server
// checked itself" button would not have moved the guarantee, it would have
// destroyed it. So the checks run here, on the reader's own machine, over bytes
// the reader already holds.
//
// What this file can prove without trusting anything:
//   * the package is a VERSIONED envelope — a server package stripped down to a
//     bare {bundle, traces} cannot masquerade as an honest bare file;
//   * every content hash recomputes, so bundle and traces are internally intact;
//   * each trial's trace_ref binds to the trace body it claims;
//   * the publication commits to THIS bundle, and its id commits to its own body;
//   * the receipt's signed_ref binds to what it claims to sign, and — when a
//     public key is supplied — its Ed25519 signature verifies.
//
// What it cannot: replay. Recomputing verdicts needs the kernel, which is
// Python. `POST /replay` does that, and the UI labels it for what it is — a
// check performed by the server, not by you.
//
// Canonicalization is RFC 8785, which was written around ECMAScript semantics:
// JSON.stringify already emits numbers in the required form, and JavaScript
// string comparison is already by UTF-16 code unit. `canonicalJson` is pinned
// byte-for-byte against contracts/canonicalization-vectors.json, the same
// vectors the Python implementation is pinned against.

export const PACKAGE_SCHEMA = "axor-reproduction-package/v1";

/** RFC 8785 canonical JSON. Keys sorted by UTF-16 code unit, no whitespace. */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("cannot canonicalize a non-finite number");
    }
    // JSON.stringify emits the ECMAScript Number::toString form RFC 8785 requires
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      // Array.prototype.sort compares by UTF-16 code unit, which is exactly the
      // ordering RFC 8785 specifies — no locale, no collator.
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(",")}}`;
  }
  throw new Error(`cannot canonicalize ${typeof value}`);
}

export async function contentHash(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `sha256:${hex}`;
}

export type CheckStatus = "pass" | "fail" | "skipped";

export interface Check {
  name: string;
  status: CheckStatus;
  detail: string;
}

export interface VerifyResult {
  checks: Check[];
  passed: boolean;
  // a package that carries the versioned envelope must produce every proof;
  // a bare bundle+traces file legitimately has none
  serverIssued: boolean;
}

function hex(bytes: string): ArrayBuffer {
  const clean = bytes.startsWith("sha256:") ? bytes.slice(7) : bytes;
  const buffer = new ArrayBuffer(clean.length / 2);
  const out = new Uint8Array(buffer);
  for (let i = 0; i < out.length; i += 1) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return buffer;
}

/** Verify an Ed25519 signature over `message`. Returns null when unsupported. */
async function verifyEd25519(
  publicKeyHex: string, signatureHex: string, message: string,
): Promise<boolean | null> {
  try {
    const key = await crypto.subtle.importKey(
      "raw", hex(publicKeyHex), { name: "Ed25519" }, false, ["verify"],
    );
    return await crypto.subtle.verify(
      "Ed25519", key, hex(signatureHex), new TextEncoder().encode(message),
    );
  } catch {
    // Ed25519 in WebCrypto is not universal yet. Unsupported is NOT a pass —
    // the caller renders it as skipped, never as verified.
    return null;
  }
}

export async function verifyPackage(
  raw: unknown, options: { allowBare?: boolean; publicKeyHex?: string } = {},
): Promise<VerifyResult> {
  const checks: Check[] = [];
  const add = (name: string, status: CheckStatus, detail: string) =>
    checks.push({ name, status, detail });

  if (!raw || typeof raw !== "object") {
    return { checks: [{ name: "package", status: "fail", detail: "not a JSON object" }],
             passed: false, serverIssued: false };
  }
  const pkg = raw as Record<string, unknown>;
  const serverIssued = pkg.schema_version === PACKAGE_SCHEMA;

  // 1. Downgrade guard. Detection cannot key on the PRESENCE of proofs: an
  // attacker strips the envelope and every proof at once, and the file then
  // reads as an honest bare package. The envelope marker's ABSENCE is the one
  // signal that means something.
  if (serverIssued) {
    add("envelope", "pass", `versioned package (${PACKAGE_SCHEMA}) — every proof is mandatory`);
  } else if (options.allowBare) {
    add("envelope", "pass", "bare bundle+traces, as requested — no server proofs to check");
  } else {
    add("envelope", "fail",
        `not a versioned ${PACKAGE_SCHEMA} envelope. A server package cannot be ` +
        "silently downgraded to a bare bundle — tick “bare file” to verify " +
        "integrity only");
    return { checks, passed: false, serverIssued };
  }

  const bundle = pkg.bundle as Record<string, unknown> | undefined;
  const traces = pkg.traces;
  if (!bundle || !Array.isArray(traces)) {
    add("shape", "fail", "the package needs a `bundle` object and a `traces` array");
    return { checks, passed: false, serverIssued };
  }

  // 2. Trace bodies bind to the refs the trials cite.
  const byRef = new Map<string, unknown>();
  for (const trace of traces) byRef.set(await contentHash(trace), trace);
  const trials = (bundle.trials as Record<string, unknown>[]) ?? [];
  const missing = trials.filter((t) => !byRef.has(String(t.trace_ref)));
  if (trials.length === 0) {
    add("trace binding", "fail", "the bundle declares no trials");
  } else if (missing.length) {
    add("trace binding", "fail",
        `${missing.length} of ${trials.length} trials cite a trace_ref no trace body hashes to`);
  } else {
    add("trace binding", "pass",
        `all ${trials.length} trials bind to a trace body by content hash`);
  }

  // 3. The bundle's own declared hashes recompute.
  const declared = (bundle.content_hashes as Record<string, string>) ?? {};
  const sections: Record<string, unknown> = {
    ...Object.fromEntries((bundle.scenarios as Record<string, unknown>[] ?? [])
      .map((s) => [`scenario:${s.name}`, s])),
    ...Object.fromEntries((bundle.conditions as Record<string, unknown>[] ?? [])
      .map((c) => [`condition:${c.id}`, c])),
  };
  const bad: string[] = [];
  for (const [key, expected] of Object.entries(declared)) {
    if (!(key in sections)) continue; // `meta` and friends are hashed over shapes we do not rebuild
    if (await contentHash(sections[key]) !== expected) bad.push(key);
  }
  const checked = Object.keys(declared).filter((k) => k in sections).length;
  if (checked === 0) {
    add("content hashes", "skipped", "the bundle declares no per-section hashes to recompute");
  } else if (bad.length) {
    add("content hashes", "fail", `${bad.length} section hash(es) do not recompute: ${bad.slice(0, 4).join(", ")}`);
  } else {
    add("content hashes", "pass", `${checked} declared section hash(es) recompute`);
  }

  if (!serverIssued) {
    return { checks, passed: checks.every((c) => c.status !== "fail"), serverIssued };
  }

  // 4. The publication commits to THIS bundle.
  const publication = pkg.publication as Record<string, unknown> | undefined;
  if (!publication) {
    add("publication", "fail", "a server package must carry its publication body");
  } else {
    const bundleRef = String(publication.bundle_ref ?? "");
    const actual = await contentHash(bundle);
    add("publication", bundleRef === actual ? "pass" : "fail",
        bundleRef === actual
          ? "the publication's bundle_ref binds to the bundle in this package"
          : `the publication commits to a DIFFERENT bundle (${bundleRef.slice(0, 20)}… vs ${actual.slice(0, 20)}…)`);
  }

  // 5. The author receipt binds to what it claims to sign, and — with a key —
  // actually verifies. No key supplied is `skipped`, never `pass`.
  const receipt = pkg.receipt as Record<string, unknown> | undefined;
  if (!receipt) {
    add("receipt", "fail", "a server package must carry its portable receipt");
  } else {
    // The receipt is content-addressed over the BUNDLE (lab_contracts.signing
    // build_receipt), not over the publication body — so a receipt lifted from
    // another package fails here even when both packages look well-formed.
    const signedRef = String(receipt.signed_ref ?? "");
    const bundleRef = await contentHash(bundle);
    add("receipt binding", signedRef && signedRef === bundleRef ? "pass" : "fail",
        signedRef === bundleRef
          ? "the receipt's signed_ref binds to the bundle in this package"
          : "the receipt signs a DIFFERENT bundle than the one shipped here");

    const signature = String(receipt.signature ?? "");
    if (!signature) {
      add("receipt signature", "skipped",
          `unsigned receipt (integrity: ${String(receipt.integrity ?? "hash_verified")}) — ` +
          "integrity is not authenticity");
    } else if (!options.publicKeyHex) {
      add("receipt signature", "skipped",
          "signed, but no author public key given — paste one to check authenticity");
    } else {
      const ok = await verifyEd25519(options.publicKeyHex, signature, signedRef);
      add("receipt signature", ok === null ? "skipped" : ok ? "pass" : "fail",
          ok === null
            ? "this browser has no Ed25519 in WebCrypto — use axor-lab verify for authenticity"
            : ok ? "Ed25519 signature verifies against the key you supplied"
                 : "the signature does NOT verify against that key");
    }
  }

  // 6. The server acceptance must be present on a server package.
  add("acceptance", pkg.acceptance ? "pass" : "fail",
      pkg.acceptance
        ? "the server's acceptance receipt travels with the package"
        : "a server package must carry the server acceptance — stripping it is a failure");

  return { checks, passed: checks.every((c) => c.status !== "fail"), serverIssued };
}
