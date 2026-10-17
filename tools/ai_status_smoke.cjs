const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN AI STATUS CONTROLS')[1].split('// END AI STATUS CONTROLS')[0];
const elements={},calls=[];let response;
const context={byId:id=>elements[id]||(elements[id]={disabled:false,textContent:''}),
 show:(id,value)=>context.byId(id).textContent=value,
 api:async(url,options)=>{calls.push({url,options});if(response instanceof Error)throw response;return response;}};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
 assert.equal(calls.length,0);
 const base={requests_used_process:1,max_requests_process:5};
 response={...base,configured:false,credential_observation:{state:'NOT_CHECKED'}};
 await context.refreshAISetup();assert.equal(elements.aitest.disabled,true);
 assert.match(elements.aiconnectorstatus.textContent,/have not been checked/);
 assert.match(elements.aiconnectorstatus.textContent,/No vault lookup/);
 assert.doesNotMatch(elements.aiconnectorstatus.textContent,/AI is OFF/);
 response={...base,configured:true,credential_observation:{state:'OBSERVED_PRESENT'}};
 await context.refreshAISetup();assert.equal(elements.aitest.disabled,false);
 assert.match(elements.aiconnectorstatus.textContent,/may be stale/);
 response={...base,configured:true,requests_used_process:5};
 await context.refreshAISetup();assert.equal(elements.aitest.disabled,true);
 response={...base,configured:false};await context.refreshAISetup();
 assert.match(elements.aiconnectorstatus.textContent,/No AI configuration is currently observed/);
 response=Error('fixture failure');await context.refreshAISetup();assert.equal(elements.aitest.disabled,true);
 assert.match(elements.aiconnectorstatus.textContent,/unavailable/);
 assert(calls.every(call=>call.url==='/api/paper/ai-provider/status'&&call.options===undefined));
 console.log('AI status: unknown/stale metadata, no automatic IO, read-only GET and request-cap/failed-read controls passed.');
})().catch(error=>{console.error(error);process.exit(1)});
