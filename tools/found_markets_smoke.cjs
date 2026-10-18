// Actual optional discovery controller; all requests intercepted.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const script=html.split('// BEGIN FOUND MARKETS')[1].split('// END FOUND MARKETS')[0];
const nodes={},calls=[];let poll,resolve,reject;
function byId(id){return nodes[id]||(nodes[id]={checked:true,hidden:false,children:[],replaceChildren(){this.children=[]},append(x){this.children.push(x)}})}
const fixture=()=>({mode:'BOUNDED_FOUND_MARKETS_RESEARCH',read_only:true,money_movement_enabled:false,paper_orders_enabled:false,live_orders_enabled:false,policy_changed:false,profitability_validated:false,enabled:false,phase:'STOPPED',reason:'USER_STOP',fresh_summaries:0,catalog_candidates:0,requests_attempted:0,selected:[]});
const context={byId,Number,JSON,portalActive:false,jsonHeaders:{},document:{createElement:()=>({})},setInterval:f=>poll=f,show:(id,t)=>byId(id).textContent=t,refreshStartupTasks:async()=>{},api:async(path,options)=>{calls.push({path,options});return new Promise((yes,no)=>{resolve=yes;reject=no})}};
vm.createContext(context);vm.runInContext(script,context);
const flush=()=>new Promise(setImmediate);
(async()=>{
 assert.equal(calls.length,0);byId('panel-todo').hidden=true;poll();assert.equal(calls.length,0);
 byId('found-markets-enabled').checked=false;await byId('found-markets-enabled').onchange();assert.equal(calls.length,0);assert.match(byId('found-markets-status').textContent,/off/);
 const reading=context.refreshFoundMarkets();assert.equal(calls[0].path,'/api/research/found-markets');assert.equal(calls[0].options,undefined);
 await context.refreshFoundMarkets();assert.equal(calls.length,1);resolve(fixture());await reading;assert.match(byId('found-markets-list').children[0].textContent,/No fresh candidates/);assert.doesNotMatch(byId('found-markets-status').textContent,/review needed/);
 const bad=context.refreshFoundMarkets();resolve({...fixture(),live_orders_enabled:true});await bad;assert.equal(byId('found-markets-list').children.length,0);assert.match(byId('found-markets-status').textContent,/could not be verified/);
 const error=context.refreshFoundMarkets();reject(Error('PRIVATE'));await error;assert.doesNotMatch(byId('found-markets-status').textContent,/PRIVATE/);
 context.portalActive=true;byId('found-markets-enabled').checked=true;
 const start=byId('found-markets-enabled').onchange();assert.equal(calls.at(-1).path,'/api/research/found-markets/control');assert.deepEqual(JSON.parse(calls.at(-1).options.body),{enabled:true});
 const before=calls.length;await byId('found-markets-enabled').onchange();assert.equal(calls.length,before);
 resolve({});await flush();resolve(fixture());await start;
 assert.equal(byId('found-markets-enabled').disabled,false);assert.ok(!calls.some(x=>/forward\/start|tiers|orders|settings/.test(x.path)));
 console.log('Found Markets: passive navigation/polling, optional control, duplicate guard, promoted response rejection, no stale/private display and no trading requests passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
