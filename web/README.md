# axor-lab web

The nine screens from `contracts/ui-backend-contract.md`, integrated against the
real screen API. React + Vite + TypeScript, no UI framework, no state library.

```
npm install
npm run build            # -> web/dist, which `axor-lab serve` serves
npm run dev              # vite dev server, proxying the API to :8871
npm test                 # the client contract + the no-fixtures rule
```

Then, from the repo root:

```
axor-lab serve --control-token <token>
```

## Rules this codebase is held to

- **One place knows an endpoint exists** (`src/lib/api.ts`). A component that
  builds its own URL is a second, unreviewed copy of the contract table.
  `src/screens/screens.test.ts` fails the build if any screen calls `fetch`.
- **No inline fixtures.** A screen is integrated when it renders only endpoint
  output. The rule is unfalsifiable by inspection — a fixture looks like
  ordinary data — so it is checked mechanically by the same test.
- **Rendered, never computed.** No rate, threshold outcome or verdict is derived
  in the browser. Every number comes from the backend, which is the one that
  published it.
- **Failure is a state.** A failed request renders an error, never an empty list
  that reads like a healthy empty workspace.
- **The palette is the tokens.** `src/tokens.css` holds the board's values; a
  hard-coded hex anywhere else fails the test.

## What is NOT built

The Suite Builder ships **YAML mode only**. RFC §13 specifies Basic, Advanced
and YAML over one manifest; the load-bearing rule is that all three edit the
same document, which is enforced server-side (`round_trips`, and every Builder
section mapping to a manifest field). A partial Basic form would be the exact
failure that rule guards against — a mode owning state another mode cannot see —
so it is absent and says so on the screen.

There is no live run progress (`SSE /runs/{id}/events` is served but not
consumed): the Runs screen reads state on load. Starting a run from the browser
needs a connected runtime, so the flow is Integrations → connect → assign.
