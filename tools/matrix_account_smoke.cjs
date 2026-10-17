// Execute the actual Matrix renderer and controls against explicit offline fixtures.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN ACCOUNT MATRIX')[1].split('// END ACCOUNT MATRIX')[0];
const elements={},draws={segments:0,dots:0};
const ctx={setTransform(){},fillRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){draws.segments++},stroke(){},arc(){draws.dots++},fill(){},measureText:s=>({width:s.length*6})};
function element(){return {textContent:'',value:'',children:[],style:{},attrs:{},clientWidth:390,getContext:()=>ctx,replaceChildren(){this.children=[]},append(e){this.children.push(e)},setAttribute(k,v){this.attrs[k]=v}}}
function byId(id){return elements[id]||(elements[id]=element())}
byId('matrix-range').value='1m';
const calls=[],sandbox={byId,document:{createElement:()=>element()},window:{devicePixelRatio:1,addEventListener(){}},portalActive:false,show:(id,s)=>byId(id).textContent=String(s),api:async(path,options)=>{calls.push({path,options});return fixture},Date,Math,Number,Set,encodeURIComponent};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const now=Date.now()/1000;
const fixture={account_initialized:true,current_revision:2,range_start:now-60,range_end:now,observations_in_range:3,downsampled:false,adjustments:[{id:2,at:now,action:'PAPER_BUY',product:'BTC-USD',quantity:'1',entry:'100',cash_delta:'-100'}],points:[0,1,2].map(i=>({at:now-12+i*6,complete:true,equity:String(1000+i*3),cash:'800',pnl:String(i*3),account_revision:2,positions:[{product:'BTC-USD',quantity:'1',unrealized_pnl:String(i*5)},{product:'ETH-USD',quantity:'2',unrealized_pnl:String(-i*2)}]}))};
sandbox.applyAccountHistory(fixture);assert.match(byId('matrix-account-summary').textContent,/1,006.00/);assert.equal(byId('matrix-singularity').attrs['aria-pressed'],'true');
byId('matrix-fractal').onclick();assert.equal(byId('matrix-fractal').attrs['aria-pressed'],'true');assert.equal(byId('matrix-account-legend').children.length,2);
byId('matrix-adjustments').value='2';byId('matrix-adjustments').onchange();assert.match(byId('matrix-adjustment-detail').textContent,/recorded quantity 1/);assert.equal(calls.length,0);
// Complete three-point series draw 4 grid lines, zero baseline and four position segments.
draws.segments=0;draws.dots=0;sandbox.renderAccountMatrix();assert.equal(draws.segments,9);assert.equal(draws.dots,6);
fixture.downsampled=true;draws.segments=0;sandbox.applyAccountHistory(fixture);assert.equal(draws.segments,5); // dots only
fixture.downsampled=false;fixture.points[1].positions=[];draws.segments=0;sandbox.applyAccountHistory(fixture);assert.equal(draws.segments,5); // no bridges across missing holdings
fixture.points[1].complete=false;fixture.points[1].missing_products=['BTC-USD','ETH-USD'];byId('matrix-singularity').onclick();draws.segments=0;sandbox.renderAccountMatrix();assert.equal(draws.segments,4); // missing equity breaks account line
(async()=>{sandbox.portalActive=true;await sandbox.refreshAccountMatrix(false);assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/matrix/account?span=1m');assert.equal(calls[0].options,undefined);console.log('Account Matrix: actual account/position series, gaps, downsampling, mode controls and read-only range/adjustment inspection passed.');})().catch(e=>{console.error(e);process.exitCode=1});
