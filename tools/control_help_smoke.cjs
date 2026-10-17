// Exercise the actual help controller without network, account IO or a browser dependency.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/portal_preview.html'),'utf8');
const source=html.split('// BEGIN CONTROL HELP')[1].split('// END CONTROL HELP')[0].split('\n').slice(1).join('\n');
const events={},windowEvents={};let observer;
class Element{
 constructor(help){this.dataset={help};this.attrs={};this.hidden=true;this.isConnected=true;this.textContent='';this.value='private input value';this.disabled=false;}
 matches(){return !!this.hovered;}
 closest(){return this.control||this;}
 getBoundingClientRect(){return this.rect||{top:20,bottom:50,left:20,right:100};}
 contains(other){return other===this||other&&other.control===this;}
 getAttribute(key){return this.attrs[key]||null;}
 setAttribute(key,value){this.attrs[key]=value;}
 removeAttribute(key){delete this.attrs[key];}
}
const panel=new Element(),text=new Element();
const document={createElement:()=>new Element(),body:new Element(),getElementById:id=>id==='control-help'?panel:text,
 addEventListener:(name,callback)=>{events[name]=callback;}};
const window={innerHeight:800,innerWidth:1200,addEventListener:(name,callback)=>{windowEvents[name]=callback;}};
class MutationObserver{constructor(callback){observer=callback;}observe(){}}
const context={document,window,MutationObserver,Set};vm.createContext(context);
const factory=html.slice(html.indexOf('function helpButton'),html.indexOf('// BEGIN CONTROL HELP'));
vm.runInContext(factory+source,context);
assert.throws(()=>context.helpButton('Too short'),/require practical help/);
// Ordinary headings/background have no matching control, including between hover sessions.
const background={closest:()=>null};
for(const relatedTarget of [null,background]){events.pointerout({target:background,relatedTarget});assert.equal(panel.hidden,true);}
events.pointerover({target:background});assert.equal(panel.hidden,true);
const control=new Element('Choose the virtual budget used by the research model. Saving does not move money.');
control.disabled=true;control.setAttribute('aria-describedby','existing-hint');
events.pointerover({target:control});assert.equal(panel.hidden,false);assert.equal(text.textContent,control.dataset.help);
assert.ok(!text.textContent.includes(control.value));assert.equal(control.getAttribute('aria-describedby'),'existing-hint control-help');
const child=new Element();child.control=control;
events.pointerout({target:control,relatedTarget:child});assert.equal(panel.hidden,false);
events.pointerout({target:control,relatedTarget:null});assert.equal(panel.hidden,true);assert.equal(text.textContent,'');assert.equal(control.getAttribute('aria-describedby'),'existing-hint');
events.focusin({target:control});assert.equal(panel.hidden,false);
events.scroll();assert.equal(panel.hidden,false);
control.rect={top:-100,bottom:-50,left:20,right:100};events.scroll();assert.equal(panel.hidden,true);delete control.rect;
events.focusin({target:control});events.focusout({target:control});assert.equal(panel.hidden,true);
events.focusin({target:control});events.keydown({key:'Escape'});assert.equal(panel.hidden,true);
for(const hide of [events.scroll,windowEvents.blur,windowEvents.resize]){events.pointerover({target:control});hide();assert.equal(panel.hidden,true);}
control.hovered=true;events.pointerover({target:control});events.scroll();assert.equal(panel.hidden,false);
control.hovered=false;events.scroll();assert.equal(panel.hidden,true);
const dynamic=context.helpButton('Cancel only the unfilled paper remainder. Existing fills and holdings remain recorded.');
assert.equal(dynamic.type,'button');
events.pointerover({target:dynamic});assert.equal(text.textContent,dynamic.dataset.help);
assert.equal(control.getAttribute('aria-describedby'),'existing-hint');
dynamic.isConnected=false;observer();assert.equal(panel.hidden,true);assert.equal(dynamic.getAttribute('aria-describedby'),null);
console.log('Control help: disabled/dynamic hover, leave, keyboard focus, Escape, scroll, blur, resize, removal and private-value isolation passed.');
