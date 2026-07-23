// Honest empty state: no server data means an explanation of how to get some —
// never fake rows pretending to be real.
import { ReactNode } from "react";
import { Info } from "lucide-react";
import { C, MONO } from "../theme";

export default function EmptyState({
  title, children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="p-4" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 10 }}>
      <div className="flex items-center gap-2 mb-2">
        <Info size={14} color={C.dim} />
        <span style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>{title}</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7, paddingLeft: 22 }}>
        {children}
      </div>
    </div>
  );
}

export function Cmd({ children }: { children: ReactNode }) {
  return (
    <pre style={{
      margin: "6px 0 0", background: C.panel2, border: `1px solid ${C.line}`,
      borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 10,
      color: C.mut, overflow: "auto", lineHeight: 1.6,
    }}>{children}</pre>
  );
}
