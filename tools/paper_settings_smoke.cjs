// Execute the actual settings controller with intercepted reads/writes only.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN PAPER SETTINGS')[1].split('// END PAPER SETTINGS')[0];
const nodes={},calls=[];let fail=true,updated=0;
const byId=id=>nodes[id]||(nodes[id]={value:'10000',textContent:''});
const context={byId,JSON,jsonHeaders:{'content-type':'application/json'},value:id=>Number(byId(id).value),
 show:(id,text)=>byId(id).textContent=text,update:async()=>updated++,api:async(path,options)=>{
 calls.push({path,options});if(fail)throw Error('PRIVATE-SERVER-DETAIL');
 return {paper_budget:1000,terrestrial_allocation:50,solar_allocation:35,universal_allocation:15};
}};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
 assert.equal(calls.length,0);assert.equal(byId('savepaper').disabled,true);await byId('savepaper').onclick();assert.equal(calls.length,0);await context.loadPaper();assert.equal(byId('savepaper').disabled,false);
 for(const id of ['paperbudget','terrapct','solarpct','univpct'])assert.equal(byId(id).value,'');
 assert.match(byId('paperstatus').textContent,/could not be verified/);assert.doesNotMatch(byId('paperstatus').textContent,/PRIVATE/);
 assert.equal(calls.length,1);assert.equal(calls[0].options,undefined);
 fail=false;await context.loadPaper();assert.equal(byId('paperbudget').value,1000);
 const writing=byId('savepaper').onclick();assert.equal(byId('savepaper').disabled,true);const inFlight=calls.length;await byId('savepaper').onclick();assert.equal(calls.length,inFlight);await writing;assert.equal(byId('savepaper').disabled,false);const saved=calls.at(-1);
 assert.equal(saved.path,'/api/paper/settings');assert.equal(saved.options.method,'PUT');
 assert.deepEqual(JSON.parse(saved.options.body),{paper_budget:1000,terrestrial_allocation:50,solar_allocation:35,universal_allocation:15});
 assert.equal(updated,1);assert.match(byId('paperstatus').textContent,/No transfer or trade/);
 fail=true;byId('paperbudget').value='500';await byId('savepaper').onclick();
 assert.equal(byId('paperbudget').value,'500');assert.equal(updated,1);
 assert.match(byId('paperstatus').textContent,/not confirmed/);assert.doesNotMatch(byId('paperstatus').textContent,/PRIVATE/);
 assert.ok(calls.every(c=>c.path==='/api/paper/settings'));
 console.log('Paper settings: failed-read clearing, private-error isolation, explicit save, retained edits on failure and no trading action passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
