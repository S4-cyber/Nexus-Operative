# v0.340.428 broker-paper open-orders fence implementation plan

1. Add failing injected regressions for an existing open order, malformed open
   order collections and changes before and after the durable request marker.
2. Add the fixed open-orders endpoint and a strict bounded empty-collection
   requirement to all three POST-adjacent risk phases.
3. Update success evidence and request caps without changing OFF defaults,
   one-shot authorization, request-head exclusion or emergency-stop ordering.
4. Run focused broker POST, inspection, open-order, durable-budget and emergency
   suites, then independent diff review.
5. Synchronize v0.340.428 app/package/installer/roadmap/evidence and run the
   required deterministic regression, packaging, scan and hosted gates before
   an expected-head publication.
