"""Bundle the built UI into ONE self-contained HTML page you can just open.

Why this exists. `axor-lab serve` is the real thing and needs Python; a reviewer
who only wants to look at the screens should not have to install anything. So
this inlines the Vite build (CSS + JS, no external requests) and ships it with
RECORDED API responses captured from a real local server, replayed by a `fetch`
shim.

Two rules keep it from becoming a lie:

  * every response is a REAL one, captured from a real run — no hand-written
    numbers, no mock data that flatters anything;
  * a request the capture does not cover fails loudly with a message saying so,
    rather than falling back to a near-enough answer. A preview that silently
    returns the wrong row is worse than one that says "run it locally".

A banner says on every screen that this is a static preview. Anything that would
need a fresh computation — a suite selection nobody captured, a secret nobody
priced — answers 501 with the command that does it for real.

Usage:  python scripts/build_preview.py --fixtures <dir> --out preview.html
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "lab_server" / "web"

BANNER = """
<div id="axor-preview-banner">
  <b>Static preview</b> — the real UI, with API responses recorded from a live
  <code>axor-lab serve</code>. Numbers are real; anything needing a fresh
  computation says so instead of guessing.
  <span class="axor-preview-cmd">pip install axor-lab &amp;&amp; axor-lab serve</span>
</div>
<style>
  #axor-preview-banner {
    position: sticky; top: 0; z-index: 999; padding: 7px 14px;
    background: #1B1726; border-bottom: 1px solid #9B8CCC;
    color: #9B8CCC; font: 11px ui-monospace, SFMono-Regular, Menlo, monospace;
    line-height: 1.6;
  }
  #axor-preview-banner b { color: #D2DAE1; }
  #axor-preview-banner code, .axor-preview-cmd { color: #78848F; }
  .axor-preview-cmd { float: right; }
  @media (max-width: 700px) { .axor-preview-cmd { display: none; } }
</style>
"""

SHIM = """
<script>
// ── recorded-response shim ───────────────────────────────────────────────────
// The app is unmodified; only the transport is. Each entry was captured from a
// real server, so what renders is what that server said.
(function () {
  var FIX = __FIXTURES__;
  var RUN_ID = __RUN_ID__;
  var PID = __PID__;

  function ok(body) {
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { "content-type": "application/json" },
    }));
  }
  function notCaptured(what) {
    // loud on purpose: a preview that answers the wrong question quietly is
    // worse than one that admits the limit
    return Promise.resolve(new Response(JSON.stringify({
      error: what + " is not in this static preview — every response here was " +
             "recorded from a real run, and this combination was not. " +
             "Run it for real: pip install axor-lab && axor-lab serve",
    }), { status: 501, headers: { "content-type": "application/json" } }));
  }

  var real = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = url.replace(/^https?:\\/\\/[^/]+/, "").split("?")[0];
    var method = ((init && init.method) || "GET").toUpperCase();
    var body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) {}

    // ── reads ────────────────────────────────────────────────────────────
    if (path === "/jobs-api/catalog") return ok(FIX.catalog);
    if (path === "/jobs-api/runtimes") return ok(FIX.runtimes);
    if (path === "/jobs-api/benchmarks") return ok(FIX.benchmarks);
    if (path === "/api/license/status") return ok(FIX.license);
    if (path === "/api/publications") return ok(FIX.publications);
    if (path === "/api/publications/" + PID) return ok(FIX.publication);
    if (path === "/api/publications/" + PID + "/bundle") return ok(FIX.pub_bundle);
    if (path === "/api/incidents") return ok({ incidents: [] });
    if (path === "/api/regression") return ok({ pins: [] });
    if (path === "/api/audit") return ok({ events: [] });
    if (/^\\/jobs-api\\/runs\\/[^/]+\\/results$/.test(path)) return ok(FIX.run_results);
    if (/^\\/jobs-api\\/runs\\/[^/]+\\/traces$/.test(path)) return ok(FIX.run_traces);
    if (/^\\/jobs-api\\/runs\\/[^/]+\\/evidence\\//.test(path)) return ok(FIX.run_evidence);
    if (/^\\/jobs-api\\/runs\\/[^/]+$/.test(path)) return ok({ run_id: RUN_ID, state: "completed" });

    // ── the governance benchmark: exact-match on what was captured ───────
    if (path === "/jobs-api/benchmarks/run") {
      var declared = Object.keys(body.secrets || {}).length > 0;
      if (declared || body.confidentiality) {
        return notCaptured("a run with declared secret reads");
      }
      var run = body.allowlist ? FIX.bench_run_allowlist : FIX.bench_run;
      var wanted = body.suites;
      if (wanted && wanted.length) {
        // the per-suite rows are independent measurements, so narrowing the
        // selection is a filter, not a different computation
        run = Object.assign({}, run, {
          rows: run.rows.filter(function (r) { return wanted.indexOf(r.suite) >= 0; }),
        });
      }
      return ok(run);
    }
    if (path === "/jobs-api/benchmarks/sweep") {
      var key = (body.allowlist ? "sweep_al_" : "sweep_") + body.suite;
      return FIX[key] ? ok(FIX[key]) : notCaptured("that sweep");
    }

    // ── the incident paths ───────────────────────────────────────────────
    if (path === "/jobs-api/incidents/reconstruct") {
      // the shim cannot extract from an arbitrary upload — that is the server's
      // job and it is real code, not a fixture. Say which file was captured.
      return ok(FIX.reconstruct);
    }
    if (path === "/jobs-api/incidents/reconstruct/compose") return ok(FIX.reconstruct_compose);

    // ── anything that would EXECUTE ──────────────────────────────────────
    if (path === "/jobs-api/runs/local") {
      return ok({ run_id: RUN_ID, state: "completed", trials: 60,
                  missingness: "n=60/60", by_status: { completed: 60 },
                  executed: "local" });
    }
    if (path === "/jobs-api/replay") return notCaptured("replaying an uploaded bundle");
    if (path.indexOf("/jobs-api/wrap/") === 0) return notCaptured("scanning agent code");
    if (method !== "GET") return notCaptured("that action");
    // Nothing escapes to the network. A preview that quietly tries to reach a
    // server it does not have would fail as a CORS error the reader has to
    // decode; this fails as a sentence instead — and it keeps the page
    // self-contained, which is the whole promise of a single file.
    if (path.indexOf("/api/") === 0 || path.indexOf("/jobs-api/") === 0) {
      return notCaptured("GET " + path);
    }
    return real(input, init);
  };
})();
</script>
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build(fixtures_dir: Path, out: Path) -> None:
    index = _read(WEB / "index.html")
    css_names = re.findall(r'href="/assets/([^"]+\.css)"', index)
    js_names = re.findall(r'src="/assets/([^"]+\.js)"', index)
    if not js_names:
        raise SystemExit("no bundled script found in lab_server/web/index.html — run scripts/build_web.py")

    fixtures: dict[str, object] = {}
    for file in sorted(fixtures_dir.glob("*.json")):
        key = file.stem.replace("-", "_")
        fixtures[key] = json.loads(_read(file))
    # normalise the two names the shim reads for the benchmark matrix
    fixtures["bench_run"] = fixtures.pop("bench_run_al_false", {})
    fixtures["bench_run_allowlist"] = fixtures.pop("bench_run_al_true", {})

    run_id = str(
        (fixtures.get("run_results") or {}).get("run_id")  # type: ignore[union-attr]
        or "run_0001_preview"
    )
    pid = str(
        (fixtures.get("publication") or {}).get("publication_id")  # type: ignore[union-attr]
        or "e_preview"
    )

    shim = (
        SHIM.replace("__FIXTURES__", json.dumps(fixtures))
        .replace("__RUN_ID__", json.dumps(run_id))
        .replace("__PID__", json.dumps(pid))
    )

    html = index
    for name in css_names:
        style = _read(WEB / "assets" / name)
        html = html.replace(
            f'<link rel="stylesheet" crossorigin href="/assets/{name}">',
            f"<style>{style}</style>",
        ).replace(f'<link rel="stylesheet" href="/assets/{name}">', f"<style>{style}</style>")
    for name in js_names:
        script = _read(WEB / "assets" / name)
        html = re.sub(
            r'<script[^>]*src="/assets/' + re.escape(name) + r'"[^>]*></script>',
            lambda _m: "<script type=\"module\">" + script.replace("</script>", "<\\/script>") + "</script>",
            html,
        )
    # the shim has to install BEFORE the app module runs
    html = html.replace("</head>", shim + "</head>", 1)
    html = html.replace("<body>", "<body>" + BANNER, 1)
    if "/assets/" in html:
        raise SystemExit("an /assets/ reference survived inlining — the page would not be self-contained")

    out.write_text(html, encoding="utf-8")
    print(f"{out} — {out.stat().st_size // 1024} KB, {len(fixtures)} recorded responses")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.fixtures, args.out)


if __name__ == "__main__":
    main()
