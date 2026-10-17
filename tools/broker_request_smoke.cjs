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
 complete({passed:true,current_replay_duplicate:true,recovered:{retained_events:6,requests:[{snapshot:{filled_quantity:'1'}}]}});
 await first;assert.equal(button.disabled,false);
 assert.match(results.at(-1),/1 BTC/);assert.match(results.at(-1),/No broker connected/);
 fail=true;await button.onclick();assert.equal(button.disabled,false);
 assert.match(results.at(-1),/unavailable/);assert(!results.at(-1).includes('PRIVATE'));
 console.log('Broker request control: explicit fixture-only GET, no auto-request, duplicate guard, retained quantity, disconnected scope and sanitized failure passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
