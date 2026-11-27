# Emergency final journal identity design

## Intent

The standalone local-paper emergency command must never report a verified
durable halt after the exact journal it cold-replayed has been replaced at the
public pathname during its final latch/root observations. The independent
emergency intent remains asserted and the halt attempt remains authoritative;
only the unsupported durable-verification claim is downgraded.

## Constraints

- Preserve OFF-by-default engagement, reducing exits, limits, account checks,
  fail-closed behavior, and the independent emergency command.
- Add no network, broker, credential, funding, cloud, retry, reset, or second
  halt operation.
- Preserve the existing historical durable confirmation when the complete data
  root itself is displaced; that already cannot produce `VERIFIED`.
- Treat journal mismatch or observation unavailability as unconfirmed when the
  original data root is still pinned.
- Add no UI control or editable parameter.

## Approaches considered

1. **Re-run the complete halt replay after the final latch sample.** Rejected:
   it adds another SQLite reopen/replay and expands the emergency command's
   bounded observation work.
2. **Hold a journal handle or writer lock through result construction.**
   Rejected: it changes storage/coordination architecture and can impede the
   independent stop path.
3. **Re-sample the saved journal identity once after final latch/root checks,
   then recheck the root identity.** Selected: both observations are read-only,
   bounded, match the existing identity model, close the reproduced false
   claims, and add no action or retry.

## Behavior

After the final canonical latch and root observations, if the original data
root is still pinned and durable replay had succeeded, the public journal must
still match the exact `order_identity` saved before replay. Only an exact
`True` match retains `durable_order_halt_confirmed`; mismatch or transient
unavailability clears it. The root is sampled again after that comparison. A
root displaced during the journal sample retains the prior durable-history
flag, clears current latch/root authority, and cannot be verified because
`data_root_path_pinned` is false.

## Verification

A real disposable SQLite regression replaces the halted journal with a
separately initialized unhalted journal from inside the second final intent
sample. Before the fix it must reproduce `VERIFIED`; after the fix it must
report `UNCONFIRMED`, keep the latch and pinned-root evidence true, clear the
durable confirmation, and cold-read the selected journal as unhalted with zero
commands. Existing root-replacement and emergency suites must remain green.
An additional deterministic regression displaces the root during the final
journal observation, moves the original halted journal into the replacement
root, and requires only historical durable evidence to survive.

This is offline M3/M4 safety evidence only. It is not native Windows,
broker-paper, sustained-trial, funding, cloud, live, or release acceptance.
