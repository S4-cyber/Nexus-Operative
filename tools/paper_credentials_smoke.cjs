const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN PAPER CREDENTIAL CONTROLS')[1].split('// END PAPER CREDENTIAL CONTROLS')[0];
const elements=new Map();
for(const id of ['paper-api-key','paper-api-secret','paper-account-expected','paper-credentials-save','paper-credentials-status','paper-account-inspect','paper-account-paced-inspect','paper-pacing-status','paper-credentials-remove','paper-observation-download'])elements.set(id,{value:'',disabled:false,listeners:{},addEventListener(kind,fn){this.listeners[kind]=fn;}});
const messages=[],requests=[],confirmations=[],downloads=[],revoked=[];
let downloadFails=false;
let accepted=true,pending=[];
const context={Blob:class{constructor(parts,options){this.text=parts.join('');this.options=options;}},URL:{createObjectURL(blob){if(downloadFails)throw Error('private failure');downloads.push(blob);return 'blob:fixture';},revokeObjectURL:url=>revoked.push(url)},setTimeout:fn=>fn(),document:{body:{appendChild:link=>{assert.equal(link.href,'blob:fixture');}},createElement:tag=>{assert.equal(tag,'a');return {click(){assert.equal(this.download,'Nexus-Paper-Observation.json');},remove(){}};}},byId:id=>{assert(elements.has(id),id);return elements.get(id);},show:(id,message)=>{assert.equal(id,'paper-credentials-result');messages.push(message);},window:{confirm:text=>{confirmations.push(text);return accepted;}},jsonHeaders:{'Content-Type':'application/json'},api:(url,options)=>{
 assert(url.startsWith('/api/execution/alpaca-paper/local-credentials/'));assert.equal(options.method,'POST');
 requests.push({url,body:JSON.parse(options.body)});
 return new Promise((resolve,reject)=>pending.push({resolve,reject}));
}};
vm.createContext(context);vm.runInContext(script,context);
const button=id=>elements.get(id), last=()=>messages.at(-1);
const safeSetup=configured=>({configured,review_required:false,ready_for_routing:false});
const crypto=require('crypto');
const canonical=value=>JSON.stringify(value&&typeof value==='object'?(Array.isArray(value)?value.map(v=>JSON.parse(canonical(v))):Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(canonical(value[k]))]))):value);
const sha=value=>crypto.createHash('sha256').update(canonical(value)).digest('hex');
function openTerms(changes={}){
 const value={mode:'REPORTED_OPEN_IOC_LIMIT_TERMS',status:'TERMS_OBSERVED_SOURCE_UNVERIFIED',scope:'QUANTITY_BTC_USD_SIMPLE_IOC_LIMIT_TERMS_NOT_PROVIDER_HOLDS_OR_EXECUTION_PRICES',reported_rows:0,supported_unique_orders:0,duplicate_rows_ignored:0,unsupported_rows:0,terms_complete:true,remaining_buy_limit_principal_cap_usd:'0',remaining_sell_quantity_btc:'0',normalized_terms_sha256:'b'.repeat(64),review_reasons:[],...Object.fromEntries(['source_attested','order_coverage_verified','holds_reconciled','spendable_funds_verified','ready_for_routing','ledger_applied','broker_connected','live_orders_enabled'].map(k=>[k,false])),...changes};
 value.summary_sha256=sha(value);return value;
}
function enrich(report){
 report={...report,source:'INJECTED_UNATTESTED',position_count:report.observed_inventory.scope_supported?(report.observed_inventory.btc_total==='0'?0:1):1,automatic_retry:false,account_fields_sha256:'a'.repeat(64),read_timing:{started_at:1000,completed_at:1000,elapsed_seconds:0}};
 if(report.open_order_terms===undefined)report.open_order_terms=openTerms();
 report.reported_inventory_sha256=sha(report.observed_inventory);
 report.observation_sha256=sha({profile:'nexus.paper-account-observation.v1',account_fields_sha256:report.account_fields_sha256,reported_inventory_sha256:report.reported_inventory_sha256,position_count:report.position_count,open_order_count:report.open_order_count,source:report.source,read_timing:report.read_timing});
 const fields=['account_id','source','checks','observed_inventory','observed_usd_ceiling','position_count','open_order_count','account_fields_sha256','reported_inventory_sha256','read_timing','observation_sha256'];
 const saved=Object.fromEntries(fields.map(k=>[k,report[k]]));saved.read_timing=Object.fromEntries(Object.entries(report.read_timing).map(([k,v])=>[k,String(v)]));
 Object.assign(saved,{profile:'nexus.portable-paper-observation.v1',observation_digest_scope:'NORMALIZED_ACCOUNT_INVENTORY_COUNTS_SOURCE_LABEL_AND_SAMPLED_TIMING_ONLY',limitations:"Unverified sequential paper observation. Hashes check content consistency, not source authenticity, complete coverage, freshness, verified holds, spendable funds or trading permission. Account identity and balances are private financial information; share only with people you trust. No credentials or raw provider bodies included.",authority:Object.fromEntries(['ready_for_routing','spendable_funds_verified','broker_account_reconciled','broker_connected','ledger_applied','live_orders_enabled','atomic_snapshot','automatic_retry','source_attested'].map(k=>[k,false]))});
 saved.manifest_sha256=sha(saved);report.portable_observation=saved;return report;
}
const observed=enrich({mode:'READ_ONLY_PAPER_INSPECTION',checks:'OBSERVED_FIELDS_COVERED',account_id:'01234567-89ab-cdef-0123-456789abcdef',observed_usd_ceiling:'900',observed_btc_available:'1.9',open_order_count:0,ready_for_routing:false,spendable_funds_verified:false,broker_account_reconciled:false,broker_connected:false,ledger_applied:false,live_orders_enabled:false,atomic_snapshot:false,observed_inventory:{cash:'1000',non_marginable_buying_power:'900',btc_total:'2',btc_available:'1.9',btc_unavailable_difference:'0.1',scope_supported:true}});
(async()=>{
 assert.equal(requests.length,0,'no automatic credential read or provider call');
 assert.equal(button('paper-observation-download').disabled,true);button('paper-observation-download').onclick();assert.equal(downloads.length,0);
 button('paper-api-key').value='private-key';button('paper-api-secret').value='private-secret';
 accepted=false;await button('paper-credentials-save').onclick();assert.equal(requests.length,0);assert.equal(button('paper-api-key').value,'');assert.equal(button('paper-api-secret').value,'');
 accepted=true;await button('paper-credentials-save').onclick();assert.equal(requests.length,0);assert.match(last(),/Enter both/);
 button('paper-api-key').value='fixture-key';button('paper-api-secret').value='fixture-secret';
 let operation=button('paper-credentials-save').onclick();assert.equal(requests.length,1);assert.equal(button('paper-api-key').value,'');assert.equal(button('paper-api-secret').value,'');
 assert.equal(requests.at(-1).body.confirm_store,true);await button('paper-credentials-save').onclick();assert.equal(requests.length,1);
 pending.shift().resolve(safeSetup(true));await operation;assert.match(last(),/saved locally/);assert.equal(button('paper-account-inspect').disabled,false);
 operation=button('paper-credentials-status').onclick();assert.deepEqual(requests.at(-1).body,{});pending.shift().resolve(safeSetup(false));await operation;assert.match(last(),/No saved/);
 accepted=false;await button('paper-account-inspect').onclick();assert.equal(requests.length,2);accepted=true;
 button('paper-account-expected').value=' expected-id ';
 operation=button('paper-account-inspect').onclick();assert.deepEqual(requests.at(-1).body,{confirm_read_only_paper:true,expected_account_id:'expected-id'});
 assert.equal(button('paper-account-inspect').disabled,true);assert.equal(button('paper-credentials-remove').disabled,false);
 await button('paper-account-inspect').onclick();assert.equal(requests.length,3);
 pending.shift().resolve(observed);await operation;assert.match(last(),/Observed USD ceiling 900/);assert.match(last(),/Reported cash USD 1000/);assert.match(last(),/BTC total 2 \/ available 1.9/);assert.match(last(),/Unavailable difference 0.1/);assert.match(last(),/NOT a verified hold/);assert.match(last(),/Spendable funds NOT verified/);assert.match(last(),/Buy limit principal cap USD 0 \/ Sell remainder BTC 0/);assert.match(last(),/NOT verified holds or execution prices/);
 assert.equal(button('paper-observation-download').disabled,false);
 accepted=false;button('paper-observation-download').onclick();assert.equal(downloads.length,0);accepted=true;
 const readsBeforeDownload=requests.length;button('paper-observation-download').onclick();
 assert.equal(downloads.length,1);assert.equal(requests.length,readsBeforeDownload);assert.deepEqual(revoked,['blob:fixture']);
 assert.deepEqual(JSON.parse(downloads[0].text),observed.portable_observation);assert(!downloads[0].text.includes('fixture-key'));
 button('paper-account-expected').listeners.input();assert.equal(button('paper-observation-download').disabled,true);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(observed);await operation;
 downloadFails=true;button('paper-observation-download').onclick();downloadFails=false;assert.match(last(),/download could not be prepared/);assert(!last().includes('private failure'));
 operation=button('paper-account-inspect').onclick();const late=pending.shift();
 assert.equal(button('paper-observation-download').disabled,true);
 const removal=button('paper-credentials-remove').onclick();assert.equal(requests.at(-1).body.confirm_remove,true);assert.equal(button('paper-credentials-remove').disabled,true);
 await button('paper-credentials-remove').onclick();assert.equal(requests.length,6);
 late.resolve(observed);await operation;assert(!last().includes('Observed USD ceiling'));assert.equal(button('paper-account-inspect').disabled,true);
 pending.shift().resolve(safeSetup(false));await removal;assert.match(last(),/No saved paper key pair/);assert.equal(button('paper-credentials-remove').disabled,false);
 operation=button('paper-account-inspect').onclick();pending.shift().reject(Error('private-secret raw backend failure'));await operation;assert.match(last(),/could not be confirmed/);assert(!last().includes('private-secret'));assert.equal(button('paper-account-inspect').disabled,false);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,ready_for_routing:true});await operation;assert.match(last(),/could not be confirmed/);
 for(const change of [
  {observed_inventory:undefined},
  {observed_inventory:{...observed.observed_inventory,cash:'private-secret'}},
  {observed_inventory:{...observed.observed_inventory,btc_available:undefined}},
  {account_id:'private-secret'}, {open_order_count:129}, {ledger_applied:true}, {atomic_snapshot:true},
  {portable_observation:{...observed.portable_observation,private_extra:'private-secret'}},
  {portable_observation:{...observed.portable_observation,account_id:'private-secret'}},
  {portable_observation:{...observed.portable_observation,authority:{...observed.portable_observation.authority,source_attested:true}}},
  {portable_observation:{...observed.portable_observation,limitations:'private-secret'}},
  {portable_observation:{...observed.portable_observation,manifest_sha256:'private-secret'}}
 ]){
  operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,...change});await operation;
  assert.match(last(),/could not be confirmed/);assert(!last().includes('private-secret'));assert.equal(button('paper-observation-download').disabled,true);
 }
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(enrich({...observed,checks:'REVIEW_REQUIRED',observed_btc_available:null,observed_usd_ceiling:null,
  observed_inventory:{cash:null,non_marginable_buying_power:null,btc_total:null,btc_available:null,btc_unavailable_difference:null,scope_supported:false}}));
 await operation;assert.match(last(),/Reported cash USD unknown/);assert.match(last(),/BTC total unknown \/ available unknown/);assert.match(last(),/Unavailable difference unknown/);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(enrich({...observed,observed_inventory:{...observed.observed_inventory,cash:'0',non_marginable_buying_power:'0',btc_total:'0',btc_available:'0',btc_unavailable_difference:'0'},observed_usd_ceiling:'0',observed_btc_available:'0'}));
 await operation;assert.match(last(),/BTC total 0 \/ available 0/);assert(!last().includes('BTC total unknown'));
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(enrich({...observed,open_order_count:2,checks:'REVIEW_REQUIRED',open_order_terms:openTerms({reported_rows:2,supported_unique_orders:2,remaining_buy_limit_principal_cap_usd:'250',remaining_sell_quantity_btc:'0.5'})}));await operation;
 assert.match(last(),/Buy limit principal cap USD 250 \/ Sell remainder BTC 0.5/);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(enrich({...observed,open_order_count:1,checks:'REVIEW_REQUIRED',open_order_terms:openTerms({reported_rows:1,unsupported_rows:1,terms_complete:false,status:'REVIEW_REQUIRED',remaining_buy_limit_principal_cap_usd:null,remaining_sell_quantity_btc:null,normalized_terms_sha256:null,review_reasons:['UNSUPPORTED_OR_INVALID_OPEN_ORDER_TERMS']})}));await operation;
 assert.match(last(),/Buy limit principal cap USD unknown \/ Sell remainder BTC unknown/);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(enrich({...observed,open_order_count:128,checks:'REVIEW_REQUIRED',open_order_terms:openTerms({reported_rows:128,supported_unique_orders:128,remaining_buy_limit_principal_cap_usd:'128000000000000000000000000'})}));await operation;
 assert.match(last(),/Buy limit principal cap USD 128000000000000000000000000/);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve(enrich({...observed,open_order_count:1,checks:'REVIEW_REQUIRED',open_order_terms:openTerms({reported_rows:1,supported_unique_orders:1,remaining_buy_limit_principal_cap_usd:'0.'+'0'.repeat(35)+'1'})}));await operation;
 assert(last().includes('Buy limit principal cap USD 0.'+'0'.repeat(35)+'1'));
 for(const change of [{source_attested:true},{private_extra:'private-secret'},{reported_rows:1},{terms_complete:false},{remaining_buy_limit_principal_cap_usd:'private-secret'}]){
  operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,open_order_terms:{...observed.open_order_terms,...change}});await operation;
  assert.match(last(),/could not be confirmed/);assert(!last().includes('private-secret'));assert.equal(button('paper-observation-download').disabled,true);
 }
 operation=button('paper-account-inspect').onclick();button('paper-account-expected').listeners.input();pending.shift().resolve(observed);await operation;
 assert.equal(button('paper-observation-download').disabled,true,'input changed during read must not restore old export');assert.equal(button('paper-account-inspect').disabled,false);
 assert(confirmations.some(x=>x.includes('No broker request')));assert(confirmations.some(x=>x.includes('does not revoke keys')));

 const pacing={account_sha256:'a'.repeat(64),before_head_sha256:'b'.repeat(64),after_head_sha256:'c'.repeat(64),events:2,sessions:1,next_allowed_at:1060,min_post_result_cooldown_seconds:60,coordination_scope:'EXACT_LOCAL_JOURNAL_ONLY_NOT_BROKER_WIDE_OR_DISTRIBUTED'};
 const paced={...observed,durable_local_pacing:true,local_pacing:pacing};
 const flags=['broker_connected','broker_paper_accepted','source_attested','source_coverage_verified','broker_wide_quota_verified','account_reconciled','holds_reconciled','spendable_funds_verified','ledger_applied','ready_for_routing','live_orders_enabled','money_movement_enabled','automatic_retry','automatic_continuation','release_approved','cloud_deployed','provider_key_permissions_verified','windows_profile_accepted'];
 const status={mode:'PAPER_ACCEPTANCE_PACING_STATUS',status:'REVIEW_REQUIRED',read_only:true,journal_present:true,account_sha256:'a'.repeat(64),head_sha256:'c'.repeat(64),sessions:1,session_capacity_remaining:255,next_allowed_at:1060,pending_read:false,retained_review_block:false,suggested_action:'RESPECT_RETAINED_COOLDOWN_AND_SEPARATELY_CONFIRM_ONE_READ',limitations:'Local only',...Object.fromEntries(flags.map(k=>[k,false]))};
 for(const id of ['paper-account-paced-inspect','paper-pacing-status']){
  const before=requests.length,consent=confirmations.length;
  button('paper-account-expected').value='';await button(id).onclick();
  button('paper-account-expected').value='bad-private-id';await button(id).onclick();
  assert.equal(requests.length,before);assert.equal(confirmations.length,consent);assert.match(last(),/expected paper account UUID/);
 }
 button('paper-account-expected').value=observed.account_id;
 accepted=false;const before=requests.length;await button('paper-account-paced-inspect').onclick();assert.equal(requests.length,before);accepted=true;
 operation=button('paper-account-paced-inspect').onclick();
 assert.equal(requests.at(-1).url.split('/').at(-1),'paced-inspect');assert.deepEqual(requests.at(-1).body,{expected_account_id:observed.account_id,confirm_read_only_paper:true});
 assert.equal(button('paper-pacing-status').disabled,true);assert.equal(button('paper-credentials-remove').disabled,false);
 pending.shift().resolve(paced);await operation;assert.match(last(),/Local pacing retained/);assert.equal(button('paper-observation-download').disabled,false);
 const consent=confirmations.length;
 operation=button('paper-pacing-status').onclick();assert.deepEqual(requests.at(-1).body,{expected_account_id:observed.account_id});assert.equal(confirmations.length,consent);
 pending.shift().resolve(status);await operation;assert.match(last(),/1 \/ 256 sessions/);assert.match(last(),/No keys read or broker request/);assert.equal(button('paper-observation-download').disabled,true);
 for(const change of [{local_pacing:{...pacing,private_extra:'private-secret'}},{local_pacing:{...pacing,events:3}},{local_pacing:{...pacing,min_post_result_cooldown_seconds:0}},{durable_local_pacing:false},{account_id:'11234567-89ab-cdef-0123-456789abcdef'}]){
  operation=button('paper-account-paced-inspect').onclick();pending.shift().resolve({...paced,...change});await operation;
  assert.match(last(),/could not be confirmed/);assert(!last().includes('private-secret'));assert.equal(button('paper-observation-download').disabled,true);
 }
 for(const change of [{private_extra:'private-secret'},{ready_for_routing:true},{pending_read:true},{sessions:257},{journal_present:false},{head_sha256:'private-secret'}]){
  operation=button('paper-pacing-status').onclick();pending.shift().resolve({...status,...change});await operation;
  assert.match(last(),/could not be confirmed/);assert.match(last(),/local pacing history needs review/);assert.match(last(),/does not read keys/);assert(!last().includes('private-secret'));
 }
 operation=button('paper-pacing-status').onclick();pending.shift().resolve({...status,pending_read:true,suggested_action:'REVIEW_RETAINED_PENDING_READ_NO_AUTOMATIC_RETRY'});await operation;assert.match(last(),/previous read is uncertain/);
 operation=button('paper-account-paced-inspect').onclick();const latePaced=pending.shift();
 const removing=button('paper-credentials-remove').onclick();latePaced.resolve(paced);await operation;
 assert(!last().includes('Local pacing retained'));assert.equal(button('paper-observation-download').disabled,true);
 pending.shift().resolve(safeSetup(false));await removing;assert.match(last(),/No saved paper key pair/);

 if(process.argv.includes('--export-fixture')){process.stdout.write(downloads[0].text);return;}
 console.log('Paper credentials: no automatic IO, explicit consent, cleared secrets, POST-only bodies, duplicate guards, removable pending reads, stale-result fencing, private allowlisted local downloads/URL cleanup and sanitized failures passed.');
})().catch(error=>{console.error(error);process.exit(1);});
