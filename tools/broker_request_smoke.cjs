/* Actual PORTAL handler with a disposable fixture response; no backend or broker. */
const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');
const html=fs.readFileSync('dashboard/portal_preview.html','utf8');
const source=html.split('// BEGIN BROKER REQUEST RECOVERY')[1].split('// END BROKER REQUEST RECOVERY')[0];
const button={disabled:false};const results=[];const calls=[];let complete;let fail=false;
const context={byId:id=>{assert.equal(id,'broker-request-fixture');return button;},show:(id,text)=>{assert.equal(id,'broker-request-result');results.push(text);},api:async url=>{
 calls.push(url);if(fail)throw Error('PRIVATE_PROVIDER_ERROR');
 return new Promise(resolve=>{complete=resolve;});
}};
vm.runInNewContext(source,context);
(async()=>{
 assert.equal(calls.length,0);const first=button.onclick();assert(button.disabled);
 await button.onclick();assert.equal(calls.length,1);
 assert.equal(calls[0],'/api/execution/alpaca-paper/request-recovery-fixture');
 const linked={source:'TEMPORARY_REQUEST_ACTIVITY_LINK_FIXTURE',passed:true,original_request_linked:true,explicit_fills_checked:true,offsetting_bad_price_detected:true,journals_unchanged:true,...Object.fromEntries(['source_attested', 'activity_coverage_verified', 'fee_coverage_complete', 'fee_execution_links_verified', 'holds_reconciled', 'broker_account_reconciled', 'spendable_funds_verified', 'ledger_applied', 'ready_for_routing', 'broker_connected', 'live_orders_enabled', 'money_movement_enabled', 'automatic_retry', 'release_approved'].map(k=>[k,false]))};
 const stepReport={source:'TEMPORARY_BOUNDED_RECOVERY_STEP_FIXTURE',passed:true,first_request_selected:true,next_request_selected:true,cooldown_review_unchanged:true,stale_head_prevented_io:true,steps_performed:2,get_requests:4,...Object.fromEntries(['automatic_continuation', 'automatic_retry', 'automatic_submission', 'ready_for_routing', 'broker_connected', 'broker_account_reconciled', 'spendable_funds_verified', 'ledger_applied', 'live_orders_enabled', 'money_movement_enabled', 'release_approved', 'actual_provider_access', 'actual_vault_access'].map(k=>[k,false]))};
 complete({bounded_recovery_step:stepReport,activity_link_review:linked,submission_review:{read_only:true,passive:true,clear_before_marker:true,recovery_required_after_marker:true,sendable:false,routing_authorized:false,capacity_reserved:false},passed:true,current_replay_duplicate:true,lookup_recovery:{attempts:2,rate_limit_wait_seconds:30,interruption_recorded:true,late_completion_rejected:true},recovered:{retained_events:6,requests:[{snapshot:{filled_quantity:'1'}}]}});
 await first;assert.equal(button.disabled,false);assert.match(results.at(-1),/Bounded next-request selection checked/);assert.match(results.at(-1),/One original-ID lookup per confirmed step/);
 assert.match(results.at(-1),/Original request and explicit fill limits checked/);assert.match(results.at(-1),/Fees and funds still unverified/);assert.match(results.at(-1),/Passive preflight checked/);assert.match(results.at(-1),/Marked attempt requires original-ID recovery/);assert.match(results.at(-1),/1 BTC/);assert.match(results.at(-1),/Lookup attempts 2/);assert.match(results.at(-1),/30 seconds/);assert.match(results.at(-1),/Interrupted lookup review recorded/);assert.match(results.at(-1),/Late reply rejected/);assert.match(results.at(-1),/No broker connected/);
 const promoted=button.onclick();complete({submission_review:{read_only:true,passive:true,clear_before_marker:true,recovery_required_after_marker:true,sendable:true,routing_authorized:false,capacity_reserved:false},passed:true,recovered:{requests:[{snapshot:{filled_quantity:'1'}}]}});await promoted;assert.match(results.at(-1),/unavailable/);assert.doesNotMatch(results.at(-1),/Passive preflight checked/);
 const badLink=button.onclick();complete({activity_link_review:{...linked,ready_for_routing:true},passed:true,recovered:{requests:[{snapshot:{filled_quantity:'1'}}]}});await badLink;assert.match(results.at(-1),/unavailable/);assert.doesNotMatch(results.at(-1),/Original request and explicit fill limits checked/);
 const badStep=button.onclick();complete({bounded_recovery_step:{...stepReport,automatic_retry:true},passed:true,recovered:{requests:[{snapshot:{filled_quantity:'1'}}]}});await badStep;assert.match(results.at(-1),/unavailable/);assert.doesNotMatch(results.at(-1),/Bounded next-request selection checked/);
 fail=true;await button.onclick();assert.equal(button.disabled,false);
 assert.match(results.at(-1),/unavailable/);assert(!results.at(-1).includes('PRIVATE'));
 const feeSource=html.split('// BEGIN BROKER FEE CASH CHECK')[1].split('// END BROKER FEE CASH CHECK')[0];
 const feeButton={disabled:false};const feeResults=[];const feeCalls=[];let feeComplete;let feeFail=false;
 const feeContext={byId:id=>{assert.equal(id,'broker-fee-fixture');return feeButton;},show:(id,text)=>{assert.equal(id,'broker-fee-result');feeResults.push(text);},api:async url=>{
  feeCalls.push(url);if(feeFail)throw Error('PRIVATE_FEE_STORAGE');return new Promise(resolve=>{feeComplete=resolve;});
 }};
 vm.runInNewContext(feeSource,feeContext);assert.equal(feeCalls.length,0);
 const feeFirst=feeButton.onclick();assert(feeButton.disabled);await feeButton.onclick();assert.equal(feeCalls.length,1);
 const feeData={input_consistency_ok:true,reasons:[],projected_balances:{USD:'1009.78011',BTC:'0'},fees_by_asset:{BTC:'0.001',USD:'0.10989'},cash_path:{declared_path_consistency_ok:true},activity_normalization:{status:'REVIEW_REQUIRED',fee_coverage_complete:false,executions:[{},{}],fee_debits:[{},{}]}};
 feeComplete(feeData);await feeFirst;assert.equal(feeButton.disabled,false);
 assert.equal(feeCalls[0],'/api/execution/alpaca-paper/fee-projection-fixture');
 assert.match(feeResults.at(-1),/COVERED IN SUPPLIED HISTORY/);assert.match(feeResults.at(-1),/spendable funds NOT verified/);assert.match(feeResults.at(-1),/2 unlinked fee debits/);assert.match(feeResults.at(-1),/coverage NOT verified/);
 const unknown=feeButton.onclick();feeComplete({...feeData,cash_path:undefined,activity_normalization:undefined});await unknown;
 assert.match(feeResults.at(-1),/REVIEW REQUIRED/);assert.match(feeResults.at(-1),/normalization unavailable; review required/);
 feeFail=true;await feeButton.onclick();assert.equal(feeButton.disabled,false);
 assert.match(feeResults.at(-1),/unavailable/);assert(!feeResults.at(-1).includes('PRIVATE'));
 console.log('Broker controls: explicit fixture-only GETs, duplicate guards, retained quantity/lookup pacing, fee-reserved funding and unlinked raw-activity scope, unknown review and sanitized failures passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
