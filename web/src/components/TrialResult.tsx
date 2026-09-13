import type { PlaygroundResult } from "../lib/api";
import { Card, Json, Tag } from "./ui";
import { Timeline } from "./Timeline";

/**
 * One trial, rendered — the trial record, its trace, and what the suite would
 * curate from it.
 *
 * Shared by the Playground screen and the Builder's "Try one trial", because
 * the RFC asks for a single-trial debugger BEFORE full execution (§13) and the
 * useful place to run one is the screen where the suite is being written. Two
 * renderers would drift on the one thing this has to be unambiguous about:
 * whether what you are looking at counted.
 */
export function TrialResult({ result }: { result: PlaygroundResult }) {
  return (
    <>
      <Card>
        <div className="row-between">
          <h3>Trial</h3>
          {/* the payload carries `counted_in_a_run` and the screen says it: a
              debugging click that quietly became evidence is the failure that
              flag exists to prevent */}
          <Tag tone={result.counted_in_a_run ? "warning" : "info"}>
            {result.counted_in_a_run ? "counted in a run" : "not counted in a run"}
          </Tag>
        </div>
        <Json value={result.trial} />
      </Card>
      {result.trace && (
        <Card>
          <h3>Trace</h3>
          <Timeline trace={result.trace} />
        </Card>
      )}
      {result.evidence_cases.length > 0 && (
        <Card>
          <h3>What the suite would curate</h3>
          <Json value={result.evidence_cases} />
        </Card>
      )}
    </>
  );
}
