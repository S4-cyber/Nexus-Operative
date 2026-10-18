// Exercise the actual tab/checklist controller; no backend, storage or network IO.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN WORKSPACE TABS')[1].split('// END WORKSPACE TABS')[0];
const nodes={},calls=[];let focused,resolveRequest,rejectRequest,draws=0;
class Node{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.hidden=false;this.value='';}
 setAttribute(k,v){this.attrs[k]=v}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=[...items]}
 focus(){focused=this}scrollIntoView(){}
}
const byId=id=>nodes[id]||(nodes[id]=new Node());
const jump=new Node('button');jump.dataset.openTab='research';
const context={byId,Date,Event:class Event{},document:{createElement:tag=>new Node(tag),querySelectorAll:()=>[jump],dispatchEvent(){}},
 show:(id,t)=>byId(id).textContent=t,helpButton:t=>{const b=new Node('button');b.dataset.help=t;return b},renderAccountMatrix:()=>draws++,api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolveRequest=yes;rejectRequest=no})}};
vm.createContext(context);vm.runInContext(script,context);
const flush=()=>new Promise(setImmediate);
(async()=>{
 byId('paperbudget').value='12345';context.selectWorkspaceTab('risk');
 assert.equal(byId('tab-risk').attrs['aria-selected'],'true');assert.equal(byId('panel-portal').hidden,true);
 context.selectWorkspaceTab('portal');assert.equal(draws,1);assert.equal(byId('paperbudget').value,'12345');assert.equal(calls.length,0);
 let prevented=false;byId('tab-portal').onkeydown({key:'End',preventDefault(){prevented=true}});
 assert.ok(prevented);assert.equal(focused,byId('tab-help'));assert.equal(byId('tab-help').tabIndex,0);
 jump.onclick();assert.equal(byId('panel-research').hidden,false);assert.equal(calls.length,0);
 context.selectWorkspaceTab('todo');assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/onboarding/tasks');assert.equal(calls[0].options,undefined);
 await context.refreshStartupTasks();assert.equal(calls.length,1); // coalesce refreshes
 resolveRequest({checked_at:1000,summary:'Read only.',items:[
  {necessity:'BASELINE',id:'engine',title:'Built-in models',group:'Start',status:'COMPLETE',checked:true,detail:'A numerical learner is included; no external provider required.',why:'The built-in tools do not need a paid account.',setup_scope:'Included',tab:'ai',target:'counciltest'},
  {necessity:'OPTIONAL',id:'found_markets',title:'Found Markets',group:'Discovery',status:'OPTIONAL',checked:false,detail:'Research only.',setup_scope:'Optional',tab:'todo',target:'found-markets-enabled'},
  {necessity:'FUTURE',id:'payment',title:'<script>untrusted</script>',group:'Future',status:'UNAVAILABLE',checked:true,detail:'Payment is unavailable and unnecessary for local paper research.',tab:'accounts',target:'preview'}]});await flush();
 const all=[];function walk(node){all.push(node);for(const child of node.children)walk(child)}walk(byId('startup-tasks'));
 assert.ok(all.some(x=>x.textContent==='Why: The built-in tools do not need a paid account.'));assert.ok(all.some(x=>x.textContent==='Included'));
 assert.ok(all.some(x=>x.textContent==='Required for baseline paper operation'));assert.ok(all.some(x=>x.textContent==='Future connected features — not required today'));
 const headings=all.filter(x=>x.tagName==='h2').map(x=>x.textContent);assert.deepEqual(Array.from(headings),['Required for baseline paper operation','Optional improvements','Future connected features — not required today']);
 const checks=all.filter(x=>x.type==='checkbox');assert.equal(checks.length,3);assert.equal(checks[0].checked,true);assert.equal(checks[1].checked,false);
 assert.ok(checks.every(x=>x.disabled&&x.dataset.help.length>30));assert.ok(all.some(x=>x.textContent==='<script>untrusted</script>'));
 const open=all.find(x=>x.textContent==='Open AI');open.onclick();assert.equal(byId('panel-ai').hidden,false);assert.equal(focused,byId('counciltest'));assert.equal(calls.length,1);
 const refresh=context.refreshStartupTasks();assert.equal(byId('startup-tasks').children.length,0);rejectRequest(Error('PRIVATE'));await refresh;
 assert.match(byId('startup-summary').textContent,/could not be verified/);assert.doesNotMatch(byId('startup-summary').textContent,/PRIVATE/);
 assert.equal(byId('startup-refresh').disabled,false);assert.equal(byId('startup-tasks').children.length,0);
 console.log('Workspace tabs: keyboard selection, retained form values, no action on navigation, automatic disabled checks, refresh coalescing and failed-read clearing passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
