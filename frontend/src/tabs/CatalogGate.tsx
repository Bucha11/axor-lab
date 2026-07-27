// One place that turns "the catalog might not be there" into "here is a
// Catalog". The experiment screens are about experiments; none of them should
// carry a spinner branch and an unreachable-server branch of its own.
import { useQuery } from "@tanstack/react-query";
import { C, MONO } from "../theme";
import { api } from "../api";
import EmptyState, { Cmd } from "../components/EmptyState";
import Experiments from "./Experiments";
import NewExperiment from "./NewExperiment";

export default function CatalogGate({ screen }: { screen: "list" | "new" }) {
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: api.catalog });

  if (catalog.isLoading) {
    return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading…</div>;
  }
  if (catalog.isError || !catalog.data) {
    return (
      <div style={{ maxWidth: 620, margin: "0 auto" }}>
        <EmptyState title="the run API is not answering">
          One command serves the UI, the catalog and the runs:
          <Cmd>axor-lab serve</Cmd>
        </EmptyState>
      </div>
    );
  }
  return screen === "new"
    ? <NewExperiment catalog={catalog.data} />
    : <Experiments catalog={catalog.data} />;
}
