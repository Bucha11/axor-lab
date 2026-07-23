// A bordered collapsible panel — the mockups' chevroned section.
import { ReactNode, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { C, MONO } from "../theme";

export default function Collapse({
  title, children, defaultOpen = false, border = C.line, color = C.mut,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  border?: string;
  color?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ background: C.panel, border: `1px solid ${border}`, borderRadius: 8 }}>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 px-4 py-3 w-full"
        style={{ background: "none", border: "none", color, fontFamily: MONO, fontSize: 11.5, cursor: "pointer", textAlign: "left" }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {title}
      </button>
      {open && <div className="px-4 pb-3" style={{ paddingLeft: 34 }}>{children}</div>}
    </div>
  );
}
