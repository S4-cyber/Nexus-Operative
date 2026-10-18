// Execute the actual Matrix renderer and controls against explicit offline fixtures.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN ACCOUNT MATRIX')[1].split('// END ACCOUNT MATRIX')[0];
const elements={},draws={segments:0,dots:0,arcs:[],labels:[]};
const ctx={setTransform(){},fillRect(){},fillText(text,x,y){draws.labels.push({text,x,y})},beginPath(){},moveTo(){},lineTo(){draws.segments++},stroke(){},arc(x,y,r){draws.dots++;draws.arcs.push({x,y,r})},fill(){},measureText:s=>({width:s.length*6})};
function element(){return {textContent:'',value:'',children:[],style:{},attrs:{},clientWidth:390,getContext:()=>ctx,replaceChildren(){this.children=[]},append(e){this.children.push(e)},setAttribute(k,v){this.attrs[k]=v}}}
function byId(id){return elements[id]||(elements[id]=element())}
byId('matrix-range').value='1m';
const calls=[],sandbox={byId,document:{createElement:()=>element()},window:{devicePixelRatio:1,addEventListener(){}},portalActive:false,show:(id,s)=>byId(id).textContent=String(s),api:async(path,options)=>{calls.push({path,options});return fixture},Date,Math,Number,Set,encodeURIComponent};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const now=Date.now()/1000;
const fixture={account_initialized:true,current_revision:2,range_start:now-60,range_end:now,observations_in_range:3,downsampled:false,adjustments:[{id:2,at:now,action:'PAPER_BUY',product:'BTC-USD',quantity:'1',entry:'100',cash_delta:'-100'}],points:[0,1,2].map(i=>({at:now-12+i*6,complete:true,equity:String(1000+i*3),cash:'800',pnl:String(i*3),account_revision:2,positions:[{product:'BTC-USD',quantity:'1',unrealized_pnl:String(i*5)},{product:'ETH-USD',quantity:'2',unrealized_pnl:String(-i*2)}]}))};
sandbox.applyAccountHistory(fixture);assert.match(byId('matrix-account-summary').textContent,/1,006.00/);assert.equal(byId('matrix-singularity').attrs['aria-pressed'],'true');
fixture.retention={total_samples:100000,archived_samples:86234,retained_limit:6000000};sandbox.applyAccountHistory(fixture);assert.match(byId('matrix-account-summary').textContent,/retained 100,000 of 6,000,000 observations \(86,234 archived\)/);
fixture.retention.storage={database_bytes:10485760,database_byte_limit:1073741824,warnings:['OBSERVATION_DISK_RESERVE_LOW']};sandbox.applyAccountHistory(fixture);assert.match(byId('matrix-account-summary').textContent,/history storage 10.0 of 1024 MiB/);assert.match(byId('matrix-account-summary').textContent,/Low free disk space/);assert.ok(!byId('matrix-account-summary').textContent.includes('OBSERVATION_DISK_RESERVE_LOW'));fixture.retention.storage.warnings=[];
byId('matrix-fractal').onclick();assert.equal(byId('matrix-fractal').attrs['aria-pressed'],'true');assert.equal(byId('matrix-account-legend').children.length,2);
byId('matrix-adjustments').value='2';byId('matrix-adjustments').onchange();assert.match(byId('matrix-adjustment-detail').textContent,/recorded quantity 1/);assert.equal(calls.length,0);
// Two current nodes join at one shared observation; six historical marks and six timeline dots remain actual samples.
assert.match(byId('matrix-account-guide').textContent,/edges show layout, not correlation/);
assert.match(byId('matrix-account-legend').children[0].textContent,/qty 1 \/ unrealized \$10.00/);
assert.equal(byId('matrix-account-chart').style.height,'510px');
draws.segments=0;draws.dots=0;draws.arcs=[];sandbox.renderAccountMatrix();assert.equal(draws.segments,12);assert.equal(draws.dots,15);
const currentNodes=draws.arcs.filter(a=>a.r===5);assert.equal(currentNodes.length,2);
assert.ok(currentNodes[0].y<124-82*.55); // BTC gain outside the zero ring
assert.ok(currentNodes[1].y<124+82*.55); // ETH loss inside the zero ring
assert.equal(draws.arcs.filter(a=>a.r===2).length,6);assert.equal(draws.arcs.filter(a=>a.r===2.5).length,6);
fixture.downsampled=true;draws.segments=0;sandbox.applyAccountHistory(fixture);assert.equal(draws.segments,8); // no temporal connections, only simultaneous geometry
fixture.downsampled=false;fixture.points[1].positions=[];draws.segments=0;sandbox.applyAccountHistory(fixture);assert.equal(draws.segments,8); // no bridges across missing holdings
const held=fixture.points[2].positions;fixture.points[2].positions=[held[0]];draws.arcs=[];sandbox.applyAccountHistory(fixture);assert.equal(draws.arcs.filter(a=>a.r===5).length,1);assert.match(byId('matrix-account-legend').children[1].textContent,/no marked holding at latest sample/);fixture.points[2].positions=held;
// A third position produces a coordinated triangle; no new observation or IO is generated.
fixture.points[2].positions.push({product:'SOL-USD',quantity:'3',unrealized_pnl:'2'});draws.arcs=[];sandbox.applyAccountHistory(fixture);assert.equal(draws.arcs.filter(a=>a.r===5).length,3);fixture.points[2].positions.pop();
byId('matrix-account-chart').clientWidth=240;draws.labels=[];sandbox.renderAccountMatrix();assert.ok(draws.labels.filter(l=>/USD$/.test(l.text)&&l.text!=='USD').every(l=>l.x>=0&&l.x+l.text.length*6<=240));
fixture.points[1].complete=false;fixture.points[1].missing_products=['BTC-USD','ETH-USD'];byId('matrix-singularity').onclick();draws.segments=0;sandbox.renderAccountMatrix();assert.equal(draws.segments,4);assert.equal(byId('matrix-account-chart').style.height,'270px'); // missing equity breaks account line
const saved=fixture.points;fixture.points=[];sandbox.applyAccountHistory(fixture);byId('matrix-fractal').onclick();assert.equal(byId('matrix-account-legend').children.length,0);fixture.points=saved;assert.equal(calls.length,0);
(async()=>{sandbox.portalActive=true;await sandbox.refreshAccountMatrix(false);assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/matrix/account?span=1m');assert.equal(calls[0].options,undefined);console.log('Account Matrix: actual account/position series, gaps, downsampling, mode controls and read-only range/adjustment inspection passed.');})().catch(e=>{console.error(e);process.exitCode=1});
