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

## The Suite Builder's three modes

Basic, Advanced and YAML edit ONE `suite/v1` document (RFC §13). The forms are
not exhaustive — `scenarios` alone is an arbitrarily deep array — and they do not
need to be: the manifest lives in one piece of state, every field writes back
through `writePath`, and a key no form renders is carried along untouched.
Switching modes moves the DOCUMENT, not the text.

`sections.test.ts` pins the property the design rests on: editing one field
never touches another, an advanced-only field survives a Basic-mode edit, and
rewriting every rendered field with its own value is a no-op.

YAML is serialized and parsed by the SERVER (`POST /suites/to-yaml`,
`POST /suites/validate-yaml` returns the parsed manifest). A parser here would
be a second implementation that can disagree about `on`, `~` and `2026-08-08`,
and the one that decides whether a run starts is the server's.

## Live run progress

The Run screen subscribes to `SSE /runs/{id}/events` and updates without a
reload. The stream closes when the run reaches a terminal state and sends `done`
first, so the browser stops rather than reconnecting to a run that will never
move again.

`EventSource` cannot set a header, so this ONE endpoint also accepts the control
token as a query parameter. That is a real trade — a token in a URL can land in
a proxy log — accepted here because the alternative is an unauthenticated stream
of run contents. It is scoped to this route; every other endpoint takes the
header.

## What is NOT built

Starting a run from the browser needs a connected runtime, so the flow is
Integrations → connect → assign; there is no "run this suite now" button for a
runtime that has not claimed the job.
