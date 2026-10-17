// Actual hub UI orchestration, intercepted responses only; no account or network IO.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN HUB CONTROLS')[1].split('// END HUB CONTROLS')[0];
const elements={},calls=[];function byId(id){return elements[id]||(elements[id]={value:'',disabled:false,textContent:''})}
byId('hub-product').value='BTC-USD';byId('hub-interval').value='3600';byId('hub-window').value='1000';
let resolveRequest;
const sandbox={byId,show:(id,s)=>byId(id).textContent=String(s),encodeURIComponent,Date,Math,api:async(path,options)=>{calls.push({path,options});return new Promise(resolve=>{resolveRequest=resolve})}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
(async()=>{
 const work=sandbox.hubResearch('build');assert.equal(calls.length,1);assert.equal(byId('hub-product').disabled,true);
 await sandbox.hubResearch('build');assert.equal(calls.length,1);
 assert.equal(calls[0].path,'/api/matrix/hub/build?product=BTC-USD&granularity=3600&limit=1000');assert.equal(calls[0].options.method,'POST');
 resolveRequest({dataset:{content_sha256:'dataset'},model_artifact:{content_sha256:'model'},model_reused:false,model:{product:'BTC-USD',granularity:3600,bars:600,forecast_as_of:1000,models:[{horizon_bars:1,horizon_seconds:3600,forecast_return_bps:1,holdout_mae_log_bps:2,zero_change_baseline_mae_log_bps:3}]},council:{engine_version:'nexus.onboard-council.v1',eligible_learners:0,disagreement:false,specialists:[{role:'trend',verdict:'ABSTAIN',reasons:['BASELINE_NOT_BEATEN_IN_EVERY_BLOCK']}]}});
 await work;assert.equal(byId('hub-product').disabled,false);assert.match(byId('hub-readout').textContent,/BTC-USD/);assert.match(byId('hub-readout').textContent,/0 of 3 learners/);assert.match(byId('hub-readout').textContent,/no order authority/);
 const read=byId('hub-signal').onclick();assert.equal(calls[1].options,undefined);assert.ok(calls[1].path.startsWith('/api/matrix/hub/signal?'));
 resolveRequest({decision:'ABSTAIN_STALE_OR_FUTURE_DATA',model_content_sha256:'model',forecast_as_of:1000,age_seconds:999999,forecasts:[]});await read;
 assert.match(byId('hub-readout').textContent,/ABSTAIN_STALE/);
 console.log('Hub controls: explicit bounded build, duplicate-click guard, selection freeze, research-only council readout and read-only cached signal passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
