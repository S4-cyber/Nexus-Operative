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
 complete({passed:true,current_replay_duplicate:true,lookup_recovery:{attempts:2,rate_limit_wait_seconds:30,interruption_recorded:true,late_completion_rejected:true},recovered:{retained_events:6,requests:[{snapshot:{filled_quantity:'1'}}]}});
 await first;assert.equal(button.disabled,false);
 assert.match(results.at(-1),/1 BTC/);assert.match(results.at(-1),/Lookup attempts 2/);assert.match(results.at(-1),/30 seconds/);assert.match(results.at(-1),/Interrupted lookup review recorded/);assert.match(results.at(-1),/Late reply rejected/);assert.match(results.at(-1),/No broker connected/);
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
