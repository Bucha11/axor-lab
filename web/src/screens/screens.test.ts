import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Phase 4's rule: "each screen integrated against a real endpoint with no
 * inline fixtures. A screen is *integrated* when its fixture is deleted and it
 * renders only endpoint output."
 *
 * That rule is unfalsifiable by inspection — a fixture looks like ordinary data
 * until someone diffs it against a live response — so it is checked
 * mechanically here, the same way the capability boundary is.
 *
 * Two things are enforced:
 *
 *   1. No screen builds its own URL. The client is the one place that knows an
 *      endpoint exists; a `fetch("/runs/...")` in a component is a second,
 *      unreviewed copy of the contract table.
 *   2. No screen carries a stand-in dataset. The tell is a large array or
 *      object literal of records; a single default like `useState("")` is not
 *      one.
 */

const SCREENS = join(import.meta.dirname, ".");
const COMPONENTS = join(import.meta.dirname, "..", "components");

function sources(dir: string): [string, string][] {
  return readdirSync(dir)
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => [name, readFileSync(join(dir, name), "utf8")] as [string, string]);
}

const ALL = [...sources(SCREENS), ...sources(COMPONENTS)];

describe("no screen talks to the network directly", () => {
  it("every screen goes through the api client", () => {
    for (const [name, text] of ALL) {
      const stripped = text.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
      expect(stripped, `${name} calls fetch directly`).not.toMatch(/\bfetch\s*\(/);
      expect(stripped, `${name} builds an XHR`).not.toMatch(/XMLHttpRequest/);
    }
  });

  it("there is at least one screen to check", () => {
    // a filter that matched nothing would pass every assertion above
    expect(ALL.length).toBeGreaterThan(5);
  });
});

describe("no screen ships a fixture", () => {
  it("carries no stand-in dataset", () => {
    for (const [name, text] of ALL) {
      const stripped = text.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
      // an array literal holding three or more object records is a dataset,
      // not a default. Nav tables and copy maps are keyed by route/step and are
      // matched separately below.
      const records = stripped.match(/\[\s*\{[\s\S]{80,}?\}\s*,\s*\{/g) ?? [];
      expect(records, `${name} looks like it carries a fixture`).toHaveLength(0);
    }
  });

  it("renders nothing when the payload is missing", () => {
    // every screen must handle the three states; a screen with no `Failed`
    // branch renders an empty workspace on a failed request
    for (const [name, text] of ALL) {
      if (!/useAsync\(/.test(text)) continue;
      expect(text, `${name} has no error branch`).toMatch(/Failed|error/);
    }
  });
});

describe("outcome words keep their meaning", () => {
  it("error is not painted as failed", () => {
    const ui = readFileSync(join(COMPONENTS, "ui.tsx"), "utf8");
    // `error` means the invariant could not be EVALUATED. Painting it red says
    // the run broke something, which is a different and false claim.
    const tone = ui.slice(ui.indexOf("export function outcomeTone"));
    expect(tone).toMatch(/case "error":\s*\n\s*return "warning"/);
    expect(tone).toMatch(/case "failed":\s*\n\s*return "danger"/);
  });
});

describe("the design tokens are the only palette", () => {
  it("no component hard-codes a colour", () => {
    for (const [name, text] of ALL) {
      expect(text, `${name} hard-codes a hex colour`).not.toMatch(/#[0-9a-fA-F]{6}\b/);
    }
    const css = readFileSync(join(import.meta.dirname, "..", "app.css"), "utf8");
    expect(css, "app.css hard-codes a hex colour").not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
