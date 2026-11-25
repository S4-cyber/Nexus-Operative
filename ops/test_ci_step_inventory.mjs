import test from 'node:test';
import assert from 'node:assert/strict';
import {review} from './ci_step_inventory.mjs';
const SHA='a'.repeat(40);
const stepsFor={
 'reachable-history-and-secrets':['Run actions/checkout@fake','Bind scan to exact source commit','Audit private artifacts in current and reachable historical objects','Install checksum-pinned Gitleaks','Scan all fetched history without logging secret values','Complete job'],
 'offline-regression-0-of-2':['Run actions/checkout@fake','Bind job to exact source commit','Compile Python source','Run complete Python regression suite','Complete job'],
 'offline-regression-1-of-2':['Run actions/checkout@fake','Bind job to exact source commit','Compile Python source','Run complete Python regression suite','Complete job'],
 'offline-release-evidence':['Run actions/checkout@fake','Bind job to exact source commit','Run bounded offline release trials','Revalidate saved offline evidence against this source','Complete job'],
 'portal-browser-help':['Run actions/checkout@fake','Bind job to exact source commit','Verify real-browser control help with isolated fixtures','Complete job'],
 'offline-ci-inventory-review':['Run actions/checkout@fake','Bind job to exact source commit','Run offline CI inventory verifier tests','Complete job'],
 'test-and-build':['Run actions/checkout@fake','Package standalone EXE','Verify EXE starts','Build installer (Inno Setup)','Verify installed application, reinstall and data-preserving uninstall','Validate installer phase evidence','Create distribution checksum manifest','Upload distribution with source and checksum manifest','Complete job']};
const spec=[['Public repository security',['reachable-history-and-secrets']],['Nexus research safety regression',['offline-regression-0-of-2','offline-regression-1-of-2','offline-release-evidence','portal-browser-help','offline-ci-inventory-review']],['Windows simulated-research build',['test-and-build']]];
function fixture(){let id=1;return {expected_commit:SHA,runs:spec.map(([name,jobs])=>{let runId=id++;return {run:{id:runId,name,head_sha:SHA,checked_out_sha:SHA,status:'completed',conclusion:'success',run_attempt:1,event:name==='Windows simulated-research build'?'push':'pull_request'},jobs:jobs.map(j=>({id:id++,run_id:runId,run_attempt:1,name:j,status:'completed',conclusion:'success',steps:stepsFor[j].map(name=>({name,status:'completed',conclusion:'success'}))}))}})}}
test('valid inventory is never release approval',()=>{let r=review(fixture());assert.equal(r.release_approved,false);assert.equal(r.workflows_reviewed,3)});
test('JSON round-trip inventory remains fully inspected',()=>{let r=review(JSON.parse(JSON.stringify(fixture())));assert.equal(r.workflows_reviewed,3);assert.equal(r.steps_inspected>0,true)});
test('review never invokes a replaced array iterator',()=>{
 const original=Array.prototype[Symbol.iterator],calls=[],input=fixture();let result;
 try{
  Object.defineProperty(Array.prototype,Symbol.iterator,{configurable:true,writable:true,value:function*(){calls.push(this)}});
  result=review(input);
 }finally{Object.defineProperty(Array.prototype,Symbol.iterator,{configurable:true,writable:true,value:original})}
 assert.equal(calls.length,0);assert.equal(result.steps_inspected>0,true);
});
test('review never invokes a replaced array every method',()=>{
 const original=Array.prototype.every,calls=[],input=fixture();let result;
 try{
  Object.defineProperty(Array.prototype,'every',{configurable:true,writable:true,value:function(){calls.push(this);return true}});
  result=review(input);
 }finally{Object.defineProperty(Array.prototype,'every',{configurable:true,writable:true,value:original})}
 assert.equal(calls.length,0);assert.equal(result.steps_inspected>0,true);
});
test('review never invokes a replaced array push method',()=>{
 const original=Array.prototype.push,input=fixture();let result;
 try{
  Object.defineProperty(Array.prototype,'push',{configurable:true,writable:true,value(){throw Error('push executed')}});
  result=review(input);
 }finally{Object.defineProperty(Array.prototype,'push',{configurable:true,writable:true,value:original})}
 assert.equal(result.steps_inspected>0,true);
});
test('review never invokes a replaced array includes method',()=>{
 const original=Array.prototype.includes,input=fixture();let result;
 try{
  Object.defineProperty(Array.prototype,'includes',{configurable:true,writable:true,value(){throw Error('includes executed')}});
  result=review(input);
 }finally{Object.defineProperty(Array.prototype,'includes',{configurable:true,writable:true,value:original})}
 assert.equal(result.steps_inspected>0,true);
});
const cases=[
 ['wrong sha', b=>b.runs[1].run.checked_out_sha='b'.repeat(40)],
 ['cancelled installer',b=>b.runs[2].run.conclusion='cancelled'],
 ['missing distribution upload',b=>b.runs[2].jobs[0].steps.splice(-2,1)],
 ['skipped critical action',b=>b.runs[2].jobs[0].steps.at(-2).conclusion='skipped'],
 ['wrong order',b=>b.runs[2].jobs[0].steps.reverse()],
 ['missing browser',b=>b.runs[1].jobs.splice(3,1)],
 ['missing regression shard',b=>b.runs[1].jobs.splice(1,1)],
 ['duplicate job',b=>b.runs[1].jobs[2].name=b.runs[1].jobs[1].name],
 ['stale attempt',b=>b.runs[1].jobs[0].run_attempt=2],
 ['unauthorized PR windows event',b=>b.runs[2].run.event='pull_request'],
 ['duplicate run',b=>b.runs[1]=structuredClone(b.runs[0])],
 ['missing security',b=>b.runs.shift()],
 ['malformed bool job id',b=>b.runs[0].jobs[0].id=true],
 ['extra fields',b=>b.runs[0].jobs[0].secret='foo'],
 ['stale checkout',b=>b.runs[0].run.head_sha='b'.repeat(40)],
 ['incomplete step',b=>b.runs[1].jobs[0].steps[2].status='in_progress'],
 ['missing regression source binding',b=>b.runs[1].jobs[0].steps.splice(1,1)],
 ['skipped evidence source binding',b=>b.runs[1].jobs[2].steps[1].conclusion='skipped'],
 ['missing browser source binding',b=>b.runs[1].jobs[3].steps.splice(1,1)],
 ['inherited bundle prototype',b=>Object.setPrototypeOf(b,{untrusted:true})],
 ['accessor bundle commit',b=>Object.defineProperty(b,'expected_commit',{enumerable:true,get(){throw Error('accessor executed')}})],
 ['nonstandard job-array prototype',b=>Object.setPrototypeOf(b.runs[1].jobs,Object.create(Array.prototype))],
 ['accessor step status',b=>Object.defineProperty(b.runs[1].jobs[0].steps[0],'status',{enumerable:true,get(){throw Error('accessor executed')}})],
 ['sparse steps despite sufficient length',b=>{b.runs[1].jobs[0].steps.length++;}],
 ['array subclass',b=>Object.setPrototypeOf(b.runs,Object.create(Array.prototype))],
 ['non-enumerable bundle field',b=>Object.defineProperty(b,'hidden',{value:true})],
 ['symbol bundle field',b=>b[Symbol('hidden')]=true],
 ['non-enumerable step field',b=>Object.defineProperty(b.runs[1].jobs[0].steps[0],'hidden',{value:true})],
 ['symbol job-array field',b=>b.runs[1].jobs[Symbol('hidden')]=true],
];
for(const [name,mutate] of cases)test('rejects '+name,()=>{let b=fixture();mutate(b);assert.throws(()=>review(b),/CI_STEP_/)});
