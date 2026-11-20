const test = require('node:test');
const assert = require('node:assert/strict');
const {init:initDishes} = require('../../ui/frontend_prototype/dishes.js');
const {init:initTelescope} = require('../../ui/frontend_prototype/telescope.js');

class Element {
  constructor() { this._value=''; this.hidden=false; this.dataset={}; this.listeners={}; this.classes=new Set(); this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name),contains:name=>this.classes.has(name)}; }
  get value() {return this._value;}
  set value(value) {this._value=String(value);}
  addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);}
  async emit(name, event={}) {for (const fn of this.listeners[name]||[]) await fn({preventDefault(){},...event});}
  setAttribute(name,value) {this[name]=value;}
  querySelectorAll() {return [];}
  replaceChildren() {}
  reportValidity() {return true;}
}
function harness() {
  const elements=new Map(), inputs=new Map(), calls=[];
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  for(const prefix of ['dish','telescope']) {
    get(prefix+'-form').hidden=true;
    get(prefix+'-form').elements={namedItem:name=>{const key=prefix+':'+name;if(!inputs.has(key))inputs.set(key,new Element());return inputs.get(key);}};
  }
  const active=new Element(); active.dataset.adminTab='telescope';
  const doc={getElementById:get,createElement:()=>new Element(),querySelector:selector=>selector==='[data-admin-tab].is-active'?active:null,querySelectorAll:()=>[]};
  const win={confirm:()=>true,addEventListener(){},DmdSession:{can_edit:true}};
  let profile='rotation', revision=1, reject=false;
  let shared={name:'Reference Observatory',latitude:53.234123,longitude:-2.305485,height:77};
  async function api(resource, options={}) {
    calls.push({resource,...options});
    if(resource==='profiles')return {payload:{profile,profiles:[{profile:'rotation',available:true},{profile:'local',available:true}]}};
    if(resource==='profile'){profile=options.body.profile;return {payload:{profile}};}
    if(resource!=='telescope/config')throw new Error('Unexpected profile-backed request '+resource);
    assert.equal(options.profile,undefined,'Observatory requests must be profile independent');
    if(options.method==='PUT') {
      if(reject)throw Object.assign(new Error('Changed'),{code:'version_conflict'});
      assert.equal(options.etag,'"'+revision+'"'); shared={...options.body};revision++;
    }
    return {payload:{config:{...shared},scope:'observatory'},etag:'"'+revision+'"'};
  }
  const dishes=initDishes(doc,win,api), telescope=initTelescope(doc,win);
  const field=name=>inputs.get('telescope:'+name);
  return {get,field,calls,win,dishes,telescope,reject:()=>reject=true,shared:()=>shared};
}

test('Telescope loads first, edits one global file and does not change the live LST until TM applies it', async()=>{
  const h=harness();
  await h.dishes.refresh();
  assert.deepEqual(h.calls.map(call=>call.resource),['profiles','telescope/config']);
  assert.equal(h.field('longitude').value,'-2.305485');
  assert.equal(h.get('telescope-save').disabled,true);
  let directClockUpdates=0;h.win.DmdSiderealClock={setConfig(){directClockUpdates++;}};
  h.field('name').value='Updated Observatory'; h.field('longitude').value='0';h.field('height').value='';
  await h.get('telescope-form').emit('input');
  assert.equal(h.dishes.admin.isDirty(),true);
  assert.equal(h.get('telescope-save').disabled,false);
  await h.get('telescope-form').emit('submit');
  assert.deepEqual(h.shared(),{name:'Updated Observatory',latitude:53.234123,longitude:0,height:null});
  assert.equal(h.dishes.admin.isDirty(),false);
  assert.match(h.get('dish-message').textContent,/all Config Profiles.*TM.*RESYNC/);
  assert.equal(directClockUpdates,0);
  h.get('dish-profile').value='local';await h.dishes.switchProfile();
  assert.equal(h.field('name').value,'Updated Observatory');
  assert.equal(h.field('longitude').value,'0');
  assert.match(h.get('dish-message').textContent,/across all Config Profiles/);
});

test('shared edits participate in discard/cancel and revision-conflict handling',async()=>{
  const h=harness();await h.dishes.refresh();
  h.field('name').value='Unsaved';
  await h.get('telescope-form').emit('input');
  h.win.confirm=()=>false;
  const count=h.calls.length;await h.dishes.refresh();
  assert.equal(h.calls.length,count);
  h.get('dish-profile').value='local';await h.dishes.switchProfile();
  assert.equal(h.calls.length,count);
  assert.equal(h.get('dish-profile').value,'rotation');
  h.reject();await h.get('telescope-form').emit('submit');
  assert.equal(h.field('name').value,'Unsaved');
  assert.match(h.get('dish-message').textContent,/changed since you opened/);
  assert.equal(h.dishes.admin.isDirty(),true);
  h.win.confirm=()=>true;await h.get('telescope-cancel').emit('click');
  assert.equal(h.field('name').value,'Reference Observatory');
  assert.equal(h.dishes.admin.isDirty(),false);
});

test('viewers can read but cannot save telescope configuration',async()=>{
  const h=harness();h.win.DmdSession.can_edit=false;await h.dishes.refresh();
  assert.equal(h.get('telescope-form').hidden,false);
  assert.equal(h.get('telescope-fields').disabled,true);
  h.field('name').value='Forbidden';
  const count=h.calls.length;await h.get('telescope-form').emit('submit');
  assert.equal(h.calls.length,count);
});
