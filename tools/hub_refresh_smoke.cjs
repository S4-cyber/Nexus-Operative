// Actual local refresh controller, intercepted API only; never starts a worker.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN LOCAL REFRESH CONTROLS')[1].split('// END LOCAL REFRESH CONTROLS')[0];
const nodes={},calls=[];let resolveRequest,rejectRequest;
function byId(id){return nodes[id]||(nodes[id]={value:'',disabled:false,textContent:''})}
byId('hub-product').value='ETH-USD';byId('hub-interval').value='300';byId('hub-window').value='10000';
const context={byId,Date,Math,Number,JSON,jsonHeaders:{'content-type':'application/json'},show:(id,v)=>byId(id).textContent=v,
 api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolveRequest=yes;rejectRequest=no})}};
vm.createContext(context);vm.runInContext(source,context);
function reply(phase,cycle_active=false){return {phase,configuration:{product:'ETH-USD',granularity:300,limit:1000,collect_public_history:false},polls:2,poll_limit:60,fits:1,fit_limit:8,unchanged_skips:1,reason:phase==='STOPPED'?'USER_STOP':null,cycle_active}}
(async()=>{
 assert.equal(calls.length,0);
 const start=context.localRefreshControl('start');assert.equal(byId('hub-refresh-start').disabled,true);
 assert.equal(calls[0].path,'/api/matrix/hub/refresh/start');assert.equal(calls[0].options.method,'POST');
 assert.deepEqual(JSON.parse(calls[0].options.body),{product:'ETH-USD',granularity:300,limit:1000,collect_public_history:false});
 await context.localRefreshControl('start');assert.equal(calls.length,1);
 resolveRequest(reply('WAITING'));await start;assert.match(byId('hub-refresh-readout').textContent,/ETH-USD.*1\/8 training attempts/);
 assert.match(byId('hub-refresh-readout').textContent,/SAVED DATA ONLY/);
 const read=context.localRefreshControl('status');assert.equal(calls[1].options,undefined);resolveRequest(reply('WAITING'));await read;
 const stop=context.localRefreshControl('stop');assert.equal(calls[2].options.method,'POST');resolveRequest(reply('STOPPING',true));await stop;
 assert.match(byId('hub-refresh-readout').textContent,/calculation is still finishing/);
 const failed=context.localRefreshControl('status');rejectRequest(Error('PRIVATE TOKEN'));await failed;
 assert.match(byId('hub-refresh-readout').textContent,/could not be confirmed/);assert.doesNotMatch(byId('hub-refresh-readout').textContent,/PRIVATE/);
 assert.equal(byId('hub-refresh-stop').disabled,false);
 byId('hub-refresh-public').checked=true;const opted=context.localRefreshControl('start');assert.equal(JSON.parse(calls.at(-1).options.body).collect_public_history,true);resolveRequest({...reply('WAITING'),public_history_enabled:true,source_requests:4,source_request_limit:12});await opted;assert.match(byId('hub-refresh-readout').textContent,/PUBLIC HISTORY OPTED IN.*4\/12/);
 console.log('Local refresh controls: explicit start/stop, bounded window, read-only status, duplicate guard, finishing state and failure privacy passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
