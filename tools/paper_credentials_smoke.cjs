const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN PAPER CREDENTIAL CONTROLS')[1].split('// END PAPER CREDENTIAL CONTROLS')[0];
const elements=new Map();
for(const id of ['paper-api-key','paper-api-secret','paper-account-expected','paper-credentials-save','paper-credentials-status','paper-account-inspect','paper-credentials-remove'])elements.set(id,{value:'',disabled:false});
const messages=[],requests=[],confirmations=[];
let accepted=true,pending=[];
const context={byId:id=>{assert(elements.has(id),id);return elements.get(id);},show:(id,message)=>{assert.equal(id,'paper-credentials-result');messages.push(message);},window:{confirm:text=>{confirmations.push(text);return accepted;}},jsonHeaders:{'Content-Type':'application/json'},api:(url,options)=>{
 assert(url.startsWith('/api/execution/alpaca-paper/local-credentials/'));assert.equal(options.method,'POST');
 requests.push({url,body:JSON.parse(options.body)});
 return new Promise((resolve,reject)=>pending.push({resolve,reject}));
}};
vm.createContext(context);vm.runInContext(script,context);
const button=id=>elements.get(id), last=()=>messages.at(-1);
const safeSetup=configured=>({configured,review_required:false,ready_for_routing:false});
const observed={mode:'READ_ONLY_PAPER_INSPECTION',checks:'OBSERVED_FIELDS_COVERED',account_id:'01234567-89ab-cdef-0123-456789abcdef',observed_usd_ceiling:'900',observed_btc_available:'1.9',open_order_count:0,ready_for_routing:false,spendable_funds_verified:false,broker_account_reconciled:false,broker_connected:false,ledger_applied:false,live_orders_enabled:false,atomic_snapshot:false,observed_inventory:{cash:'1000',non_marginable_buying_power:'900',btc_total:'2',btc_available:'1.9',btc_unavailable_difference:'0.1',scope_supported:true}};
(async()=>{
 assert.equal(requests.length,0,'no automatic credential read or provider call');
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
 pending.shift().resolve(observed);await operation;assert.match(last(),/Observed USD ceiling 900/);assert.match(last(),/Reported cash USD 1000/);assert.match(last(),/BTC total 2 \/ available 1.9/);assert.match(last(),/Unavailable difference 0.1/);assert.match(last(),/NOT a verified hold/);assert.match(last(),/Spendable funds NOT verified/);
 operation=button('paper-account-inspect').onclick();const late=pending.shift();
 const removal=button('paper-credentials-remove').onclick();assert.equal(requests.at(-1).body.confirm_remove,true);assert.equal(button('paper-credentials-remove').disabled,true);
 await button('paper-credentials-remove').onclick();assert.equal(requests.length,5);
 late.resolve(observed);await operation;assert(!last().includes('Observed USD ceiling'));assert.equal(button('paper-account-inspect').disabled,true);
 pending.shift().resolve(safeSetup(false));await removal;assert.match(last(),/No saved paper key pair/);assert.equal(button('paper-credentials-remove').disabled,false);
 operation=button('paper-account-inspect').onclick();pending.shift().reject(Error('private-secret raw backend failure'));await operation;assert.match(last(),/could not be confirmed/);assert(!last().includes('private-secret'));assert.equal(button('paper-account-inspect').disabled,false);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,ready_for_routing:true});await operation;assert.match(last(),/could not be confirmed/);
 for(const change of [
  {observed_inventory:undefined},
  {observed_inventory:{...observed.observed_inventory,cash:'private-secret'}},
  {observed_inventory:{...observed.observed_inventory,btc_available:undefined}},
  {account_id:'private-secret'}, {open_order_count:129}, {ledger_applied:true}, {atomic_snapshot:true}
 ]){
  operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,...change});await operation;
  assert.match(last(),/could not be confirmed/);assert(!last().includes('private-secret'));
 }
 operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,observed_btc_available:null,observed_usd_ceiling:null,
  observed_inventory:{cash:null,non_marginable_buying_power:null,btc_total:null,btc_available:null,btc_unavailable_difference:null,scope_supported:false}});
 await operation;assert.match(last(),/Reported cash USD unknown/);assert.match(last(),/BTC total unknown \/ available unknown/);assert.match(last(),/Unavailable difference unknown/);
 operation=button('paper-account-inspect').onclick();pending.shift().resolve({...observed,observed_inventory:{...observed.observed_inventory,cash:'0',non_marginable_buying_power:'0',btc_total:'0',btc_available:'0',btc_unavailable_difference:'0'},observed_usd_ceiling:'0',observed_btc_available:'0'});
 await operation;assert.match(last(),/BTC total 0 \/ available 0/);assert(!last().includes('BTC total unknown'));
 assert(confirmations.some(x=>x.includes('No broker request')));assert(confirmations.some(x=>x.includes('does not revoke keys')));
 console.log('Paper credentials: no automatic IO, explicit consent, cleared secrets, POST-only bodies, duplicate guards, removable pending reads, stale-result fencing and sanitized failures passed.');
})().catch(error=>{console.error(error);process.exit(1);});
