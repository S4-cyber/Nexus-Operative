// Actual read-only diagnostic controller; intercepted response, no exchange requests.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN FEED EFFICIENCY CONTROLS')[1].split('// END FEED EFFICIENCY CONTROLS')[0];
const nodes={},calls=[];let resolveRequest,rejectRequest;
function byId(id){return nodes[id]||(nodes[id]={disabled:false,textContent:''})}
const context={byId,show:(id,v)=>byId(id).textContent=v,Error,
 api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolveRequest=yes;rejectRequest=no})}};
const roadmapSource=html.split('// BEGIN RELEASE ROADMAP CONTROLS')[1].split('// END RELEASE ROADMAP CONTROLS')[0];
vm.createContext(context);vm.runInContext(source+roadmapSource,context);
(async()=>{
 assert.equal(calls.length,0);const first=context.refreshFeedEfficiency();assert.equal(byId('feed-efficiency').disabled,true);
 await context.refreshFeedEfficiency();assert.equal(calls.length,1);
 assert.equal(calls[0].path,'/api/public-data/efficiency');assert.equal(calls[0].options,undefined);
 resolveRequest({read_only:true,requests:12,transport_attempts:4,cache_hits:6,shared_returns:2,transport_failures:0,wait_timeouts:0,active_requests:0,active_request_limit:3,mean_successful_transport_ms:120});await first;
 assert.match(byId('feed-efficiency-result').textContent,/12 reader calls \/ 4 transport attempts/);
 assert.match(byId('feed-efficiency-result').textContent,/no trade or profitability evidence/);assert.equal(byId('feed-efficiency').disabled,false);
 const failed=context.refreshFeedEfficiency();rejectRequest(Error('PRIVATE'));await failed;
 assert.match(byId('feed-efficiency-result').textContent,/could not be confirmed/);assert.doesNotMatch(byId('feed-efficiency-result').textContent,/PRIVATE|12 reader/);
 const empty=context.refreshFeedEfficiency();resolveRequest({read_only:true,mean_successful_transport_ms:null});await empty;
 assert.match(byId('feed-efficiency-result').textContent,/not measured/);
 const roadmap=byId('release-roadmap-load').onclick();assert.equal(calls.at(-1).path,'/api/roadmap');assert.equal(calls.at(-1).options,undefined);
 resolveRequest({read_only:true,revision:'0.86.0',branch:'development/windows-portal-foundation',delivery_stages:[{id:'D5',title:'Secure deposits',status:'NOT_IMPLEMENTED',progress:'Preview only.',remaining:'Reconcile spendable funds.'},{id:'D9',title:'1.0 production release',status:'BLOCKED',progress:'Prerelease.',remaining:'Independent acceptance.'}],production_scope:'Separate live authorization required.',milestones:[],strategic_goals:[],next_steps:[],supporting_track:'Cloud planned.',speed_target:'No broker speed validation.',resume:'Read source roadmap.'});await roadmap;
 assert.match(byId('release-roadmap-content').textContent,/D5 \/ Secure deposits \/ NOT_IMPLEMENTED/);assert.match(byId('release-roadmap-content').textContent,/D9 \/ 1.0 production release \/ BLOCKED/);
 console.log('Feed efficiency controls: read-only GET, no auto-fetch, duplicate guard, aggregate timing scope and failed-read privacy passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
