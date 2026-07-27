// The device that keeps these screens short.
//
// The Lab has a lot worth explaining — an enum restricts as well as supersedes,
// a declared secret arms a sticky floor, ground-truth replay is a lower bound —
// and the last version put all of it on screen at once. The result read as a
// wall: everything explained, nothing understood.
//
// So: one short line is visible, the paragraph is one click away. Nothing is
// hidden from anyone who wants it, and nobody has to read it to get started.
import { useState } from "react";
import { C, MONO } from "../theme";

export default function Why({ children, label = "why?" }: {
  children: React.ReactNode; label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        style={{
          background: "none", border: "none", padding: 0, cursor: "pointer",
          color: open ? C.violet : C.dim, fontFamily: MONO, fontSize: 10,
          textDecoration: "underline", textUnderlineOffset: 3,
        }}
      >
        {label}
      </button>
      {open && (
        <div style={{
          fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7,
          marginTop: 8, paddingLeft: 10, borderLeft: `2px solid ${C.line}`,
        }}>
          {children}
        </div>
      )}
    </>
  );
}
