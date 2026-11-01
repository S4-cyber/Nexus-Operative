const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN RELEASE ROADMAP CONTROLS')[1].split('// END RELEASE ROADMAP CONTROLS')[0];
const nodes={},calls=[];let resolve,reject;
const byId=id=>nodes[id]||(nodes[id]={});
function progress(){const weights=[10,15,15,20,10,10,10,5,5],done=[85,55,70,35,0,10,10,35,0];return {method:'WEIGHTED_ENGINEERING_ESTIMATE_V1',as_of:'2026-10-18',basis_commit:'0'.repeat(40),confidence:'LOW',estimated_release_date:null,release_ready:false,release_approved:false,live_orders_enabled:false,money_movement_enabled:false,weights_total:100,total_stages:9,fully_accepted_stages:0,weighted_index_percent:38,implementation_estimate_percent:40,uncertainty_low_percent:30,uncertainty_high_percent:45,methodology:'Manual relative-scope estimate.',update_policy:'No automatic increase from tests.',stages:weights.map((w,i)=>({id:'D'+(i+1),weight_percent:w,implementation_estimate_percent:done[i],acceptance_status:'OPEN'}))}}
function fixture(){
 const stages=progress().stages.map((row,i)=>({id:row.id,title:'Stage '+(i+1),status:'PARTIAL',progress:'Foundation.',remaining:'Independent acceptance.',depends_on:i?['D'+i]:[]}));
 const plan={read_only:true,purpose:'REMAINING_ACCEPTANCE_WORK_NOT_EXECUTION_AUTHORITY',release_approved:false,live_orders_enabled:false,money_movement_enabled:false,next_major_gate:'D4',stages:stages.map(stage=>({stage_id:stage.id,unaccepted_dependencies:[...stage.depends_on],steps:Array.from({length:stage.id==='D4'?4:3},(_,i)=>({id:stage.id+'.'+(i+1),title:'Acceptance check',required_evidence:'Required evidence for '+stage.id+'.'+(i+1),state:'REVIEW_REQUIRED'}))}))};
 return {read_only:true,revision:'fixture',branch:'fixture',progress_estimate:progress(),acceptance_plan:plan,delivery_stages:stages,milestones:[],next_steps:[]};
}
const context={byId,Set,Number,Math,show:(id,s)=>byId(id).textContent=s,api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolve=yes;reject=no})}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 assert.equal(calls.length,0);const read=byId('release-roadmap-load').onclick();await byId('release-roadmap-load').onclick();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/roadmap');assert.equal(calls[0].options,undefined);resolve(fixture());await read;
 assert.match(byId('release-roadmap-content').textContent,/REMAINING ACCEPTANCE CHECKLIST/);assert.match(byId('release-roadmap-content').textContent,/Unaccepted prerequisites: D3/);assert.match(byId('release-roadmap-content').textContent,/\[ \] D4\.4 \/ Acceptance check: Required evidence for D4\.4/);assert.equal((byId('release-roadmap-content').textContent.match(/\[ \] D[1-9]\.[1-4]/g)||[]).length,28);
 assert.match(byId('release-progress-summary').textContent,/About 40%/);assert.match(byId('release-progress-summary').textContent,/0\/9/);assert.match(byId('release-progress-summary').textContent,/not a launch date/);
 for(const mutate of [p=>p.release_approved=true,p=>p.implementation_estimate_percent=90,p=>p.stages[0].weight_percent=80,p=>p.fully_accepted_stages=9,p=>p.stages[1].id='D1',p=>p.uncertainty_high_percent=20]){
  const d=fixture();mutate(d.progress_estimate);const read=byId('release-roadmap-load').onclick();resolve(d);await read;assert.match(byId('release-progress-summary').textContent,/could not be verified/);assert.doesNotMatch(byId('release-progress-summary').textContent,/About 40%/);
 }
 for(const mutate of [
  d=>d.acceptance_plan.release_approved=true,d=>d.acceptance_plan.live_orders_enabled=true,d=>d.acceptance_plan.money_movement_enabled=true,
  d=>d.acceptance_plan.read_only=false,d=>d.acceptance_plan.purpose='EXECUTE',d=>d.acceptance_plan.next_major_gate='D9',
  d=>d.acceptance_plan.stages.pop(),d=>d.acceptance_plan.stages[0].stage_id='D2',d=>d.acceptance_plan.stages[1].unaccepted_dependencies=[],
  d=>d.acceptance_plan.stages[0].steps[0].state='ACCEPTED',d=>d.acceptance_plan.stages[0].steps[1].id='D1.1',
  d=>d.acceptance_plan.stages[0].steps[0].required_evidence='',d=>d.acceptance_plan.stages[0].steps[0].title='x'.repeat(161),
  d=>d.acceptance_plan.stages[0].steps[0].required_evidence='x'.repeat(2001),d=>d.acceptance_plan.stages[0].steps[0].title='bad\nline',
  d=>d.acceptance_plan.stages[0].steps[0].private_extension='PRIVATE',d=>d.acceptance_plan.stages[0].steps=[],
  d=>d.acceptance_plan.stages[0].steps=Array(9).fill(d.acceptance_plan.stages[0].steps[0]),d=>d.acceptance_plan=null,
  d=>d.delivery_stages[1].depends_on=['D9'],d=>d.delivery_stages[1].depends_on=['D1','D1'],
  d=>d.delivery_stages[1].status='ACCEPTED',d=>d.delivery_stages[1].id='D1',d=>d.delivery_stages.pop()
 ]){
  const d=fixture();mutate(d);const read=byId('release-roadmap-load').onclick();resolve(d);await read;
  assert.match(byId('release-progress-summary').textContent,/could not be verified/);
  assert.doesNotMatch(byId('release-roadmap-content').textContent,/D4\.4|PRIVATE|Required evidence/);
  assert.equal(byId('release-roadmap-load').disabled,false);
 }
 const literal=fixture();literal.acceptance_plan.stages[0].steps[0].title='<img src=x onerror=alert(1)>';
 const textRead=byId('release-roadmap-load').onclick();resolve(literal);await textRead;
 assert.match(byId('release-roadmap-content').textContent,/<img src=x onerror=alert\(1\)>/);assert.equal(byId('release-roadmap-content').innerHTML,undefined);
 const noPlan=fixture();delete noPlan.acceptance_plan;const oldPlan=byId('release-roadmap-load').onclick();resolve(noPlan);await oldPlan;
 assert.match(byId('release-roadmap-content').textContent,/Detailed acceptance checklist unavailable/);assert.doesNotMatch(byId('release-roadmap-content').textContent,/\[ \] D1\.1/);
 const legacy=fixture();delete legacy.progress_estimate;const old=byId('release-roadmap-load').onclick();resolve(legacy);await old;assert.match(byId('release-progress-summary').textContent,/No rough engineering estimate/);
 const error=byId('release-roadmap-load').onclick();reject(Error('PRIVATE'));await error;assert.doesNotMatch(byId('release-roadmap-content').textContent,/PRIVATE/);assert.equal(byId('release-roadmap-load').disabled,false);
 console.log('Release progress: weighted estimates, 28 read-only acceptance steps, prerequisite binding, malformed/authority rejection, literal text, duplicate guard, legacy absence and stale/private failure clearing passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
