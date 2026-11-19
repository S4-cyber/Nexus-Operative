#!/usr/bin/env node
/** Offline supplemental CI step inventory. Never attests GitHub identity or approves release. */
import { readFileSync, lstatSync, openSync, fstatSync, closeSync } from 'node:fs';
import { basename } from 'node:path';
const MAX = 524288;
const SHA = /^[0-9a-f]{40}$/;
const REQUIRED = Object.freeze({
  'Public repository security': {'reachable-history-and-secrets': [
    'Run actions/checkout@', 'Bind scan to exact source commit',
    'Audit private artifacts in current and reachable historical objects',
    'Install checksum-pinned Gitleaks',
    'Scan all fetched history without logging secret values', 'Complete job']},
  'Nexus research safety regression': {
    'offline-regression-0-of-2': ['Run actions/checkout@', 'Bind job to exact source commit', 'Compile Python source', 'Run complete Python regression suite', 'Complete job'],
    'offline-regression-1-of-2': ['Run actions/checkout@', 'Bind job to exact source commit', 'Compile Python source', 'Run complete Python regression suite', 'Complete job'],
    'offline-release-evidence': ['Run actions/checkout@', 'Bind job to exact source commit', 'Run bounded offline release trials', 'Revalidate saved offline evidence against this source', 'Complete job'],
    'portal-browser-help': ['Run actions/checkout@', 'Bind job to exact source commit', 'Verify real-browser control help with isolated fixtures', 'Complete job']},
  'Windows simulated-research build': {'test-and-build': [
    'Run actions/checkout@', 'Package standalone EXE', 'Verify EXE starts',
    'Build installer (Inno Setup)', 'Verify installed application, reinstall and data-preserving uninstall',
    'Validate installer phase evidence', 'Create distribution checksum manifest',
    'Upload distribution with source and checksum manifest', 'Complete job']}
});
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
function assert(condition, code) { if (!condition) throw new Error('CI_STEP_' + code); }
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function positiveInt(n) { return Number.isSafeInteger(n) && n > 0; }
function exactKeys(obj, keys) {return Object.keys(obj).length === keys.length && keys.every(k=>own(obj,k));}
function matches(step, token) {return token.endsWith('@') ? step.startsWith(token) : step === token;}
export function review(bundle) {
  assert(object(bundle) && exactKeys(bundle,['expected_commit','runs']), 'SHAPE');
  assert(typeof bundle.expected_commit === 'string' && SHA.test(bundle.expected_commit), 'COMMIT');
  assert(Array.isArray(bundle.runs) && bundle.runs.length === 3, 'RUNS');
  let runNames=new Set(), runIds=new Set(), jobIds=new Set(), count=0;
  for (const item of bundle.runs) {
    assert(object(item)&&exactKeys(item,['run','jobs']), 'RECORD');
    const r=item.run;
    assert(object(r)&&exactKeys(r,['id','name','head_sha','checked_out_sha','status','conclusion','run_attempt','event']), 'RUN_SHAPE');
    assert(own(REQUIRED,r.name)&&!runNames.has(r.name), 'RUN_NAME');
    assert(positiveInt(r.id)&&!runIds.has(r.id)&&positiveInt(r.run_attempt), 'RUN_ID');
    runNames.add(r.name);runIds.add(r.id);
    assert(r.checked_out_sha===bundle.expected_commit&&r.status==='completed'&&r.conclusion==='success', 'RUN_NOT_GREEN');
    assert(typeof r.head_sha==='string'&&SHA.test(r.head_sha), 'HEAD');
    assert(r.event==='push'||r.event==='pull_request'||r.event==='workflow_dispatch', 'EVENT');
    assert(r.head_sha===bundle.expected_commit || (r.name==='Windows simulated-research build'&&r.event==='workflow_dispatch'), 'HEAD_MISMATCH');
    if(r.name==='Windows simulated-research build') assert(r.event!=='pull_request','WINDOWS_EVENT');
    const required=REQUIRED[r.name];
    assert(Array.isArray(item.jobs)&&item.jobs.length===Object.keys(required).length,'JOBS');
    let names=new Set();
    for(const j of item.jobs){
      assert(object(j)&&exactKeys(j,['id','run_id','run_attempt','name','status','conclusion','steps']),'JOB_SHAPE');
      assert(positiveInt(j.id)&&!jobIds.has(j.id)&&j.run_id===r.id&&j.run_attempt===r.run_attempt,'JOB_ID');
      jobIds.add(j.id);
      assert(own(required,j.name)&&!names.has(j.name),'JOB_NAME');names.add(j.name);
      assert(j.status==='completed'&&j.conclusion==='success','JOB_NOT_GREEN');
      assert(Array.isArray(j.steps)&&j.steps.length>=required[j.name].length&&j.steps.length<=50,'STEPS');
      let last=-1;
      for(const token of required[j.name]){
        const matchesAt=[];
        for(let i=0;i<j.steps.length;i++) if(object(j.steps[i])&&typeof j.steps[i].name==='string'&&matches(j.steps[i].name,token)) matchesAt.push(i);
        assert(matchesAt.length===1 && matchesAt[0]>last,'STEP_MISSING_OR_ORDER');last=matchesAt[0];
        const step=j.steps[last];
        assert(exactKeys(step,['name','status','conclusion'])&&step.status==='completed'&&step.conclusion==='success','STEP_NOT_GREEN');
      }
      assert(j.steps.every(s=>object(s)&&exactKeys(s,['name','status','conclusion'])&&typeof s.name==='string'&&s.status==='completed'&&['success','skipped'].includes(s.conclusion)),'STEP_BAD');
      count+=j.steps.length;
    }
  }
  return {status:'CI_STEP_INVENTORY_ONLY',expected_commit:bundle.expected_commit,workflows_reviewed:3,steps_inspected:count,release_approved:false,broker_accepted:false,native_windows_accepted:false,live_trading_enabled:false,limitations:'Untrusted exported summaries, not GitHub attestation or source/package/provenance/owner acceptance. Skipped optional steps may remain; review mandatory steps and actual logs independently.'};
}
function main(){
 try{
   assert(process.argv.length===3,'ARGUMENTS'); const path=process.argv[2], st=lstatSync(path);
   assert(st.isFile()&&!st.isSymbolicLink()&&st.nlink===1&&st.size>0&&st.size<=MAX,'FILE');
   const fd=openSync(path,'r');let bytes;
   try{const opened=fstatSync(fd);assert(opened.isFile()&&opened.dev===st.dev&&opened.ino===st.ino&&opened.size===st.size,'FILE_CHANGED');bytes=readFileSync(fd);const after=fstatSync(fd);assert(after.size===st.size&&after.mtimeMs===st.mtimeMs,'FILE_CHANGED')}finally{closeSync(fd)}
   assert(bytes.length===st.size&&bytes.length<=MAX,'SIZE');
   const result=review(JSON.parse(bytes.toString('utf8')));console.log(JSON.stringify(result));
 }catch{console.log('CI_STEP_REVIEW_REQUIRED');process.exitCode=2;}
}
if(process.argv[1] && basename(process.argv[1])==='ci_step_inventory.mjs') main();
