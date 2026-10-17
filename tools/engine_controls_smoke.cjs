// Execute the actual engine/throttle controller with intercepted APIs, no account IO.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN ENGINE CONTROLS')[1].split('// END ENGINE CONTROLS')[0];
const nodes={},calls=[];let forward=false,confirm=true,pendingStart,holdStart=false;
function byId(id){return nodes[id]||(nodes[id]={value:'',checked:false,disabled:false,attrs:{},setAttribute(k,v){this.attrs[k]=v}})}
byId('hub-product').value='BTC-USD';byId('hub-interval').value='3600';byId('hub-window').value='1000';byId('hub-refresh-public').checked=true;byId('forward-tier').value='terrestrial';
const context={byId,Math,Number,JSON,portalActive:false,halted:false,jsonHeaders:{'content-type':'application/json'},
 window:{confirm:()=>confirm},show:(id,v)=>byId(id).textContent=v,update:async()=>{},localRefreshControl:async()=>{},refreshAccountMatrix:()=>{},
 api:async(path,options)=>{calls.push({path,options});
  if(path==='/api/portal')context.portalActive=JSON.parse(options.body).enabled;
  if(path==='/api/paper/forward/start'){if(holdStart)return new Promise((resolve,reject)=>pendingStart={resolve,reject});forward=true;}
  if(path==='/api/paper/forward/stop')forward=false;
  if(path==='/api/simulate/halt'){context.halted=true;forward=false;}
  if(path==='/api/simulate/halt/reset')context.halted=false;
  return {};
 },refreshForward:async()=>{vm.runInContext('paperStateKnown=true;paperRunning='+forward+';syncTradingControls()',context)}};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
 assert.equal(calls.length,0);context.syncTradingControls();assert.equal(byId('engage').disabled,true);
 assert.match(html, /id="hub-refresh-public" type="checkbox" checked/);
 await byId('activate').onclick();assert.equal(calls.length,1);
 assert.deepEqual(JSON.parse(calls[0].options.body),{enabled:true,product:'BTC-USD',granularity:3600,limit:1000,collect_public_history:true});
 assert.equal(byId('engage').disabled,false);assert.equal(forward,false);
 confirm=false;await context.engagePaper();assert.equal(calls.length,1);confirm=true;
 await context.engagePaper();assert.equal(calls.at(-1).path,'/api/paper/forward/start');assert.equal(forward,true);
 assert.equal(byId('disengage').disabled,false);assert.equal(byId('engage').attrs['aria-pressed'],'true');
 await context.disengagePaper();assert.equal(forward,false);assert.equal(context.portalActive,true);
 holdStart=true;const starting=context.engagePaper();assert.equal(byId('engage').disabled,true);
 assert.equal(byId('halt').disabled,false);await byId('halt').onclick();
 assert.equal(calls.at(-1).path,'/api/simulate/halt');assert.equal(context.portalActive,true);assert.equal(byId('engage').disabled,true);
 pendingStart.reject(Error('ENGAGE_INTERRUPTED_BY_CUT_OR_PAUSE'));await starting;
 const before=calls.length;await byId('reset-halt').onclick();
 assert.equal(calls.length,before+1);assert.equal(calls.at(-1).path,'/api/simulate/halt/reset');
 assert.equal(context.portalActive,true);assert.equal(forward,false);assert.equal(byId('engage').disabled,false);
 byId('hub-refresh-public').checked=false;await byId('activate').onclick();
 assert.equal(JSON.parse(calls.at(-1).options.body).enabled,false);assert.equal(JSON.parse(calls.at(-1).options.body).collect_public_history,false);
 assert.equal(byId('engage').disabled,true);
 console.log('Engine controls: default public scope, no automatic Engage, confirmation, separate Disengage, emergency during pending Engage, reset without restart and engine off passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
