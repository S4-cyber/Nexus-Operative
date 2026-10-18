const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN RELEASE ROADMAP CONTROLS')[1].split('// END RELEASE ROADMAP CONTROLS')[0];
const nodes={},calls=[];let resolve,reject;
const byId=id=>nodes[id]||(nodes[id]={});
function progress(){const weights=[10,15,15,20,10,10,10,5,5],done=[85,55,70,35,0,10,10,35,0];return {method:'WEIGHTED_ENGINEERING_ESTIMATE_V1',as_of:'2026-10-18',basis_commit:'0'.repeat(40),confidence:'LOW',estimated_release_date:null,release_ready:false,release_approved:false,live_orders_enabled:false,money_movement_enabled:false,weights_total:100,total_stages:9,fully_accepted_stages:0,weighted_index_percent:38,implementation_estimate_percent:40,uncertainty_low_percent:30,uncertainty_high_percent:45,methodology:'Manual relative-scope estimate.',update_policy:'No automatic increase from tests.',stages:weights.map((w,i)=>({id:'D'+(i+1),weight_percent:w,implementation_estimate_percent:done[i],acceptance_status:'OPEN'}))}}
const fixture=()=>({read_only:true,revision:'fixture',branch:'fixture',progress_estimate:progress(),delivery_stages:[],milestones:[],next_steps:[]});
const context={byId,Set,Number,Math,show:(id,s)=>byId(id).textContent=s,api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolve=yes;reject=no})}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 assert.equal(calls.length,0);const read=byId('release-roadmap-load').onclick();await byId('release-roadmap-load').onclick();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/roadmap');assert.equal(calls[0].options,undefined);resolve(fixture());await read;
 assert.match(byId('release-progress-summary').textContent,/About 40%/);assert.match(byId('release-progress-summary').textContent,/0\/9/);assert.match(byId('release-progress-summary').textContent,/not a launch date/);
 for(const mutate of [p=>p.release_approved=true,p=>p.implementation_estimate_percent=90,p=>p.stages[0].weight_percent=80,p=>p.fully_accepted_stages=9,p=>p.stages[1].id='D1',p=>p.uncertainty_high_percent=20]){
  const d=fixture();mutate(d.progress_estimate);const read=byId('release-roadmap-load').onclick();resolve(d);await read;assert.match(byId('release-progress-summary').textContent,/could not be verified/);assert.doesNotMatch(byId('release-progress-summary').textContent,/About 40%/);
 }
 const legacy=fixture();delete legacy.progress_estimate;const old=byId('release-roadmap-load').onclick();resolve(legacy);await old;assert.match(byId('release-progress-summary').textContent,/No rough engineering estimate/);
 const error=byId('release-roadmap-load').onclick();reject(Error('PRIVATE'));await error;assert.doesNotMatch(byId('release-roadmap-content').textContent,/PRIVATE/);assert.equal(byId('release-roadmap-load').disabled,false);
 console.log('Release progress: weighted estimate consistency, duplicate guard, no authority/date promotion, legacy absence and private/stale failure clearing passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
