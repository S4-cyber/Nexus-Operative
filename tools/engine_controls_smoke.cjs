// Actual engine/throttle controller with intercepted local preflight and start APIs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN ENGINE CONTROLS')[1].split('// END ENGINE CONTROLS')[0];
const nodes={},calls=[];let forward=false,confirm=true,pendingStart,holdStart=false,holdCheck=false,pendingCheck,confirmations=0;
function clean(){return {mode:'READ_ONLY_PAPER_START_PREFLIGHT',read_only:true,tier:'terrestrial',policy_id:'btc_momentum_experiment_v1',local_checks_clear:true,blockers:[],entry_mode:'NEW_ENTRIES',will_prepare_unused_account:true,
 automatic_start:false,automatic_repair:false,running_permission_granted:false,external_owner_checked:false,fresh_prices_checked:false,strategy_validated:false,provider_contacted:false,live_orders_enabled:false,money_movement_enabled:false}}
let fixture=clean();
function byId(id){return nodes[id]||(nodes[id]={value:'',checked:false,disabled:false,attrs:{},setAttribute(k,v){this.attrs[k]=v}})}
byId('hub-product').value='BTC-USD';byId('hub-interval').value='3600';byId('hub-window').value='1000';byId('hub-refresh-public').checked=true;byId('forward-tier').value='terrestrial';
const context={byId,Math,Number,JSON,portalActive:false,halted:false,jsonHeaders:{'content-type':'application/json'},
 window:{confirm:()=>{confirmations++;return confirm}},show:(id,v)=>byId(id).textContent=v,update:async()=>{},localRefreshControl:async()=>{},refreshAccountMatrix:()=>{},
 api:async(path,options)=>{calls.push({path,options});
  if(path.startsWith('/api/paper/forward/preflight?')){if(holdCheck)return new Promise(resolve=>pendingCheck=resolve);return fixture}
  if(path==='/api/portal')context.portalActive=JSON.parse(options.body).enabled;
  if(path==='/api/paper/forward/start'){if(holdStart)return new Promise((resolve,reject)=>pendingStart={resolve,reject});forward=true;}
  if(path==='/api/paper/forward/stop')forward=false;
  if(path==='/api/simulate/halt'){context.halted=true;forward=false;}
  if(path==='/api/simulate/halt/reset')context.halted=false;
  return {};
 },refreshForward:async()=>{vm.runInContext('paperStateKnown=true;paperRunning='+forward+';syncTradingControls()',context)}};
vm.createContext(context);vm.runInContext(source,context);
const flush=()=>new Promise(setImmediate),starts=()=>calls.filter(x=>x.path==='/api/paper/forward/start').length;
(async()=>{
 context.halted=true;context.renderEngineStatus({emergency_cut_state:'PENDING',emergency_cut_pending:true});assert.equal(byId('reset-halt').disabled,true);assert.match(byId('status').textContent,/CUT PENDING/);
 context.renderEngineStatus({emergency_cut_state:'UNCONFIRMED',emergency_cut_pending:false});assert.match(byId('status').textContent,/CUT UNCONFIRMED/);
 context.renderEngineStatus({emergency_cut_state:'VERIFIED',emergency_cut_pending:false});assert.match(byId('status').textContent,/CUT VERIFIED/);context.halted=false;
 assert.equal(calls.length,0);context.syncTradingControls();assert.equal(byId('engage').disabled,true);
 await byId('activate').onclick();assert.equal(calls.length,1);
 assert.deepEqual(JSON.parse(calls[0].options.body),{enabled:true,product:'BTC-USD',granularity:3600,limit:1000,collect_public_history:true});
 confirm=false;await context.engagePaper();assert.equal(starts(),0);assert.equal(calls.at(-1).options,undefined);confirm=true;
 fixture={...clean(),local_checks_clear:false,will_prepare_unused_account:false,blockers:['TIER_NOT_SELECTED']};const dialogs=confirmations;
 await context.engagePaper();assert.equal(starts(),0);assert.equal(confirmations,dialogs);assert.match(byId('paper-start-check').textContent,/tier is OFF/);
 for(const bad of [null,{...clean(),live_orders_enabled:true},{...clean(),tier:'solar'},{...clean(),blockers:['PRIVATE-FIXTURE']},{...clean(),local_checks_clear:false}]){
  fixture=bad;await context.engagePaper();assert.equal(starts(),0);assert.doesNotMatch(byId('paper-start-check').textContent,/PRIVATE/);
 }
 fixture=clean();holdCheck=true;const checking=context.engagePaper();const count=calls.length;await context.engagePaper();assert.equal(calls.length,count);
 assert.equal(byId('engage').disabled,true);await byId('halt').onclick();pendingCheck(clean());await checking;assert.equal(starts(),0);holdCheck=false;
 await byId('reset-halt').onclick();assert.equal(context.portalActive,true);assert.equal(forward,false);
 holdCheck=true;const changing=context.engagePaper();byId('forward-tier').value='solar';pendingCheck(clean());await changing;assert.equal(starts(),0);byId('forward-tier').value='terrestrial';holdCheck=false;
 await context.engagePaper();assert.equal(starts(),1);assert.equal(forward,true);assert.equal(byId('disengage').disabled,false);
 await context.disengagePaper();assert.equal(forward,false);assert.equal(context.portalActive,true);
 holdStart=true;const starting=context.engagePaper();await flush();assert.ok(pendingStart);assert.equal(byId('engage').disabled,true);
 await byId('halt').onclick();assert.equal(calls.at(-1).path,'/api/simulate/halt');pendingStart.reject(Error('PRIVATE-FIXTURE'));await starting;
 assert.doesNotMatch(byId('paper-start-check').textContent,/PRIVATE/);await byId('reset-halt').onclick();assert.equal(forward,false);
 byId('hub-refresh-public').checked=false;await byId('activate').onclick();assert.equal(JSON.parse(calls.at(-1).options.body).enabled,false);assert.equal(byId('engage').disabled,true);
 console.log('Engine controls: read-only preflight, useful blockers, malformed/promoted reply rejection, duplicate/tier-change fences, consent and emergency during check/start passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
