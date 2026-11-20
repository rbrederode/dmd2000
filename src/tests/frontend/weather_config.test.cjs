const test=require('node:test');
const assert=require('node:assert/strict');
const {init:initDishes}=require('../../ui/frontend_prototype/dishes.js');
const {init:initWeather}=require('../../ui/frontend_prototype/weather_config.js');
class Element {
  constructor(){this._value='';this.checked=false;this.hidden=false;this.listeners={};this.dataset={};this.classes=new Set();this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key),contains:key=>this.classes.has(key)};}
  get value(){return this._value;} set value(value){this._value=String(value);}
  addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
  async emit(name,event={}){for(const fn of this.listeners[name]||[])await fn({preventDefault(){},...event});}
  append(...items){(this.children||=[]).push(...items);}
  setAttribute(name,value){this[name]=value;}querySelectorAll(){return [];}replaceChildren(){this.children=[];}reportValidity(){return true;}focus(){}
}
function harness(){
  const elements=new Map(),inputs=new Map(),calls=[];
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const field=(form,name)=>{const key=form+':'+name;if(!inputs.has(key))inputs.set(key,new Element());return inputs.get(key);};
  for(const form of ['dish','weather-config-station','weather-config-settings']){get(form+'-form').hidden=true;get(form+'-form').elements={namedItem:name=>field(form,name)};}
  const active=new Element();active.dataset.adminTab='weather';
  const doc={getElementById:get,createElement:()=>new Element(),querySelector:selector=>selector==='[data-admin-tab].is-active'?active:null,querySelectorAll:()=>[]};
  const win={confirm:()=>true,DmdSession:{can_edit:true},addEventListener(){}};
  const settings={weather_enabled:true,threshold_timeout:30,retention_period:300,summary_window:60,threshold_wind_avg:10,threshold_wind_gust:20,threshold_wind_count:3,threshold_precipitation:10};
  const data={rotation:{items:[{ws_id:'ws001',location:'<First site>',latitude:53,longitude:-2,elevation:77}],settings:{...settings},revision:1},local:{items:[],settings:{...settings,retention_period:600},revision:1}};
  let profile='rotation', conflict=false;
  const clone=value=>JSON.parse(JSON.stringify(value));
  async function api(resource,options={}){
    calls.push({resource,...clone(options)});
    if(resource==='profiles')return{payload:{profile,profiles:[{profile:'rotation',available:true},{profile:'local',available:true}]}};
    if(resource==='profile'){profile=options.body.profile;return{payload:{profile}};}
    if(options.profile!==profile)throw Object.assign(new Error('Profile changed'),{code:'profile_changed'});
    const store=data[profile];const etag=()=> '"'+store.revision+'"';
    if(options.method){if(conflict||options.etag!==etag())throw Object.assign(new Error('Changed'),{code:'version_conflict'});}
    if(resource==='weather-stations'&&!options.method)return{payload:{profile,weather_stations:clone(store.items),settings:clone(store.settings),driver_types:{UNKNOWN:{driver_poll_period:1000},MODBUS:{driver_poll_period:1000,port:'/dev/ttyUSB0',slave_address:2,timeout:1,debug:false,precipitation_register:null},ADS1115:{driver_poll_period:1000,adc_backend:'ads1115',adc_channel:0,voltage_min:0,voltage_max:3.3}}},etag:etag()};
    if(resource==='weather-settings'){Object.assign(store.settings,options.body);store.revision++;return{payload:{profile,settings:clone(store.settings)},etag:etag()};}
    if(resource==='weather-stations'&&options.method==='POST'){store.items.push(clone(options.body));store.revision++;return{payload:{profile,weather_station:clone(options.body)},etag:etag()};}
    const id=resource.split('/')[1],index=store.items.findIndex(item=>item.ws_id===id);
    if(index<0)throw new Error('Missing station');
    if(options.method==='DELETE'){store.items.splice(index,1);store.revision++;return{payload:{profile,deleted_id:id},etag:etag()};}
    Object.assign(store.items[index],options.body);store.revision++;return{payload:{profile,weather_station:clone(store.items[index])},etag:etag()};
  }
  const dishes=initDishes(doc,win,api),weather=initWeather(doc,win);
  return{get,calls,win,dishes,weather,data,station:name=>field('weather-config-station',name),setting:name=>field('weather-config-settings',name),conflict:()=>conflict=true};
}

test('Weather reads stations and policy, saves either form without discarding the other draft',async()=>{
  const h=harness();await h.dishes.refresh();
  assert.equal(h.station('location').value,'<First site>');
  assert.match(h.get('weather-config-rows').innerHTML,/&lt;First site&gt;/);
  assert.equal(h.setting('retention_period').value,'300');
  h.station('location').value='Edited site';h.setting('retention_period').value='900';
  await h.get('weather-config-station-form').emit('input');
  assert.equal(h.dishes.admin.isDirty(),true);
  await h.get('weather-config-settings-form').emit('submit');
  assert.equal(h.station('location').value,'Edited site');
  assert.equal(h.weather.dirty(),true);
  await h.get('weather-config-station-form').emit('submit');
  assert.equal(h.weather.dirty(),false);
  assert.equal(h.data.rotation.settings.retention_period,900);
  assert.equal(h.data.rotation.items[0].location,'Edited site');
  h.station('latitude').value='0';h.setting('summary_window').value='120';
  await h.get('weather-config-station-form').emit('submit');
  assert.equal(h.setting('summary_window').value,'120');
  await h.get('weather-config-settings-form').emit('submit');
  assert.equal(h.data.rotation.items[0].latitude,0);
  assert.equal(h.data.rotation.settings.summary_window,120);
  assert.match(h.get('dish-message').textContent,/RESYNC DM.*WS/);
});

test('Weather supports new stations, filtering, keyboard selection and confirmed deletion',async()=>{
  const h=harness();await h.dishes.refresh();
  await h.get('weather-config-new').emit('click');
  h.station('ws_id').value='ws002';h.station('location').value='Second site';h.station('latitude').value='54';h.station('longitude').value='-3';h.station('elevation').value='90';
  await h.get('weather-config-station-form').emit('submit');
  assert.equal(h.data.rotation.items.length,2);
  assert.equal(h.station('ws_id').readOnly,true);
  h.get('weather-config-filter').value='Second';await h.get('weather-config-filter').emit('input');
  assert.match(h.get('weather-config-rows').innerHTML,/ws002/);
  assert.doesNotMatch(h.get('weather-config-rows').innerHTML,/ws001/);
  h.get('weather-config-filter').value='';await h.get('weather-config-filter').emit('input');
  await h.get('weather-config-rows').emit('keydown',{key:'Enter',target:{closest:()=>({dataset:{weatherStation:'ws001'}})}});
  assert.equal(h.station('ws_id').value,'ws001');
  h.win.confirm=()=>false;const count=h.calls.length;await h.get('weather-config-delete').emit('click');assert.equal(h.calls.length,count);
  h.win.confirm=()=>true;await h.get('weather-config-delete').emit('click');
  assert.deepEqual(h.data.rotation.items.map(item=>item.ws_id),['ws002']);
  await h.get('weather-config-delete').emit('click');
  assert.equal(h.get('weather-config-station-form').hidden,true);
  assert.equal(h.get('weather-config-settings-form').hidden,false);
});

test('Weather guards unsaved profile changes, preserves conflict drafts and isolates profiles',async()=>{
  const h=harness();await h.dishes.refresh();
  h.setting('retention_period').value='999';h.win.confirm=()=>false;
  h.get('dish-profile').value='local';const count=h.calls.length;await h.dishes.switchProfile();
  assert.equal(h.calls.length,count);assert.equal(h.get('dish-profile').value,'rotation');
  h.win.confirm=()=>true;await h.get('weather-config-settings-cancel').emit('click');
  h.get('dish-profile').value='local';await h.dishes.switchProfile();
  assert.equal(h.setting('retention_period').value,'600');
  assert.equal(h.get('weather-config-station-form').hidden,true);
  h.setting('threshold_wind_count').value='4';await h.get('weather-config-settings-form').emit('submit');
  assert.equal(h.data.local.settings.threshold_wind_count,4);assert.equal(h.data.rotation.settings.threshold_wind_count,3);
  h.conflict();h.setting('threshold_timeout').value='99';await h.get('weather-config-settings-form').emit('submit');
  assert.equal(h.setting('threshold_timeout').value,'99');assert.equal(h.weather.dirty(),true);
  assert.match(h.get('dish-message').textContent,/changed since you opened/);
});

test('viewers can inspect weather but cannot create, delete or save settings',async()=>{
  const h=harness();h.win.DmdSession.can_edit=false;await h.dishes.refresh();
  assert.equal(h.get('weather-config-station-fields').disabled,true);
  assert.equal(h.get('weather-config-settings-fields').disabled,true);
  const count=h.calls.length;h.setting('retention_period').value='999';h.station('location').value='Forbidden';
  await h.get('weather-config-settings-form').emit('submit');await h.get('weather-config-station-form').emit('submit');
  await h.get('weather-config-new').emit('click');await h.get('weather-config-delete').emit('click');
  assert.equal(h.calls.length,count);
});


test('station driver fields and simulation are saved per station with nested polling',async()=>{
  const h=harness();await h.dishes.refresh();
  h.station('driver_type').value='MODBUS';await h.station('driver_type').emit('change');
  h.station('sim_mode').value='calm';h.station('driver_poll_period').value='250';
  h.weather.driverInputs.get('port').value='/dev/test-sensor';
  h.weather.driverInputs.get('slave_address').value='3';
  h.weather.driverInputs.get('debug').checked=true;
  await h.get('weather-config-station-form').emit('submit');
  const item=h.data.rotation.items[0];
  assert.equal(item.driver_type,'MODBUS');assert.equal(item.sim_mode,'calm');
  assert.equal(item.driver_config.driver_poll_period,250);
  assert.equal(item.driver_config.port,'/dev/test-sensor');
  assert.equal(item.driver_config.slave_address,3);assert.equal(item.driver_config.debug,true);
  assert.equal(item.driver_config.precipitation_register,null);
  assert.equal(item.driver_poll_period,undefined);
  h.station('driver_type').value='ADS1115';await h.station('driver_type').emit('change');
  assert.equal(h.weather.driverInputs.has('port'),false);
  assert.equal(h.weather.driverInputs.has('adc_channel'),true);
  assert.equal(h.station('driver_poll_period').value,'1000');
  h.weather.driverInputs.get('adc_channel').value='2';
  await h.get('weather-config-station-form').emit('submit');
  assert.equal(h.data.rotation.items[0].driver_config.port,undefined);
  assert.equal(h.data.rotation.items[0].driver_config.adc_channel,2);
});
