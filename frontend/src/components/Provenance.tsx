// The provenance status chip — the review's point carried into the UI: a
// verified run must never look like an uploaded JSON. Three states, composed
// from the publication's three independent axes (origin / integrity /
// reproductions), never one hardcoded badge.
import { BadgeCheck, CircleDot } from "lucide-react";
import { C, MONO } from "../theme";
import { ProvenanceAxes } from "../api";

export interface Status {
  label: string;
  color: string;
  note: string;
  icon: typeof BadgeCheck;
}

export function statusOf(axes: ProvenanceAxes): Status {
  if (axes.reproductions.verified > 0) {
    return {
      label: "independently reproduced", color: C.steel, icon: BadgeCheck,
      note: "re-run by a third party (verified attestation)",
    };
  }
  if (axes.origin === "lab") {
    return {
      label: "Lab-executed", color: C.green, icon: BadgeCheck,
      note: "ran on Lab infra",
    };
  }
  return {
    label: "self-reported", color: C.amber, icon: CircleDot,
    note: "local run, bundle uploaded — integrity-hashed, not independently run",
  };
}

export const STATUS_LEGEND: Status[] = [
  { label: "Lab-executed", color: C.green, icon: BadgeCheck, note: "ran on Lab infra" },
  { label: "independently reproduced", color: C.steel, icon: BadgeCheck, note: "re-run by a third party" },
  {
    label: "self-reported", color: C.amber, icon: CircleDot,
    note: "local run, bundle uploaded — integrity-hashed, not independently run",
  },
];

export function StatusChip({ axes }: { axes: ProvenanceAxes }) {
  const st = statusOf(axes);
  const Icon = st.icon;
  return (
    <span className="flex items-center gap-1.5" title={st.note}
      style={{ fontFamily: MONO, fontSize: 9.5, color: st.color }}>
      <Icon size={11} /> {st.label}
    </span>
  );
}
