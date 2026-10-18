const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN TRIAL READINESS')[1].split('// END TRIAL READINESS')[0];
const nodes={},calls=[];let resolve,reject;
const byId=id=>nodes[id]||(nodes[id]={value:'72'});
const flags=['source_attested','market_realism_authenticated','account_reconciled','broker_paper_accepted','strategy_validated','sustained_forward_accepted','release_approved','live_orders_enabled','money_movement_enabled','automatic_start','external_owner_checked'];
const fixture=()=>({mode:'READ_ONLY_PAPER_TRIAL_READINESS',read_only:true,hours:72,status:'NO_RETAINED_HISTORY',history_present:false,review:null,after:'2026-01-01T00:00:00Z',until:'2026-01-04T00:00:00Z',quote_capacity:{retained:0,limit:100000,remaining:100000,estimated_additional_observations:51841,room_for_additional_requested_window:true},...Object.fromEntries(flags.map(k=>[k,false]))});
const context={byId,Number,Math,Object,JSON,show:(id,s)=>byId(id).textContent=s,api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolve=yes;reject=no})}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 assert.equal(calls.length,0);const read=byId('trial-review').onclick();await byId('trial-review').onclick();assert.equal(calls.length,1);assert.equal(calls[0].options,undefined);assert.equal(calls[0].path,'/api/paper/forward/trial-readiness?hours=72');
 resolve(fixture());await read;assert.match(byId('trial-result').textContent,/No retained observations/);assert.match(byId('trial-result').textContent,/51841/);assert.equal(byId('trial-review').disabled,false);
 let r=fixture();r.status='REVIEW_REQUIRED';r.history_present=true;r.review={mode:'RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW',coverage_fraction:'1.000000',declared_coverage_target_met:true,review_reasons:['SOURCE_LABELS_AND_HASHES_UNATTESTED'],...Object.fromEntries(flags.map(k=>[k,false]))};
 const full=byId('trial-review').onclick();resolve(r);await full;assert.match(byId('trial-result').textContent,/independent acceptance is still required/);
 for(const bad of [{...fixture(),live_orders_enabled:true},{...fixture(),quote_capacity:{...fixture().quote_capacity,remaining:1}},{...r,review:{...r.review,review_reasons:['PRIVATE_ERROR']}}]){
  const read=byId('trial-review').onclick();resolve(bad);await read;assert.match(byId('trial-result').textContent,/could not be verified/);assert.doesNotMatch(byId('trial-result').textContent,/PRIVATE/);
 }
 const failed=byId('trial-review').onclick();reject(Error('PRIVATE'));await failed;assert.doesNotMatch(byId('trial-result').textContent,/PRIVATE/);assert.ok(calls.every(c=>c.options===undefined));
 console.log('Trial readiness: explicit/coalesced read, no-history guidance, capacity scope, no coverage-to-acceptance promotion, false-authority validation and private-error isolation passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
