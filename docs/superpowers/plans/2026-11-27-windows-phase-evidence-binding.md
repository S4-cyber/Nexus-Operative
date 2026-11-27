# Windows phase evidence binding implementation plan

1. Add failing tests for exact raw phase-log byte/digest output.
2. Add failing installer-result tests for exact phase-review byte/digest and
   raw-phase lineage fields.
3. Add failing distribution tests for phase-review byte equality and manifest
   inclusion.
4. Add failing owner-profile tests for the shipped phase-review artifact and
   complete digest chain.
5. Add failing workflow tests requiring copy, manifest and upload ordering.
6. Implement the minimum closed-schema propagation in the four review tools
   and Windows release workflow.
7. Run focused red/green verification, then release/version/roadmap/evidence
   checks, complete deterministic regression, packaging, secret scan and
   independent review.
8. Publish only after a fresh expected-head check and update the draft review
   stack and GRI-11 with observed evidence.
