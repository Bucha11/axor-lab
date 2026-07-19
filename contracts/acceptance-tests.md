# Axor Lab — Acceptance Tests (v1 / MVP)

End-to-end criteria that make the MVP "done." Each maps to the vertical slice.

1. **Validation rejects bad scenarios.** A scenario with a predicate naming a non-existent tool, or no untrusted-field injection vector, or no egress sink, or an unresolved `$inputs.x`, is rejected at author time with a specific error. (scenario.schema §comment)
2. **Simulated execution is safe.** Running banking-exfil-01 with a `side_effecting` `send_money` moves no real money; the ledger_stub records the attempt. Real execution requires opt-in + guards.
3. **Trace carries lineage.** The governed trial's trace shows `v_recipient` with `sources=[external_read:read_txns...]` and `arg_bindings.recipient = v_recipient`. (trace.schema)
4. **EvidenceCase renders three modes** from that trace, and the counterfactual mode is labeled as such (not an observed twin). (claims.md)
5. **Replay is exact.** `axor lab replay` recomputes the DENY over the frozen trace, bit-identical, twice, on two machines, with the pinned kernel.
6. **Statistics are honest.** ASR uses Wilson; the paired test is McNemar over stored discordant pairs; a continuous metric's CI narrows from n=10 to n=100; n<10 shows "inconclusive"; missing trials show denominator. (statistics.md)
7. **Claims are typed.** The publication carries exactly one exactly_replayable claim (verdict) and one statistically_reproducible claim (aggregate); no behavioral delta is labeled exact. (claims.md)
8. **Bundle round-trips.** `bundle/v1` → publish → the public page → re-download → `replay` reproduces the same verdicts; content hashes verify.
9. **Provenance is multidimensional.** The publication shows origin × integrity × reproductions independently; a reproduction added later increments the count without changing origin.
10. **Regression surfaces change.** Re-running the pinned trace under a hypothetical kernel that flips the verdict surfaces the change as "differs from pinned expected", not a silent pass.

When all ten pass on the vertical slice, running locally, the MVP core exists.
