/* Profile weather stations and shared monitoring policy; uses the common Admin guards. */
(function () {
  const STATION = ['ws_id','location','latitude','longitude','elevation'];
  const SETTINGS = ['weather_enabled','threshold_timeout','retention_period','summary_window','threshold_wind_avg','threshold_wind_gust','threshold_wind_count','threshold_precipitation'];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  function init(doc, win, admin = win.DmdAdmin) {
    const $ = id => doc.getElementById('weather-config-'+id);
    if (!$('workspace') || !admin) return;
    const stationForm=$('station-form'), settingsForm=$('settings-form');
    const station=name=>stationForm.elements.namedItem(name), setting=name=>settingsForm.elements.namedItem(name);
    const driverInputs=new Map();
    const state={driverTypes:{UNKNOWN:{driver_poll_period:1000}},profile:'',items:[],settings:null,selected:null,original:null,isNew:false,etag:null,loaded:false,busy:false,stationBaseline:'',settingsBaseline:''};
    const stationSnapshot=()=>JSON.stringify([...[...STATION,'driver_type','sim_mode','driver_poll_period'].map(name=>station(name).value),...Array.from(driverInputs,([key,input])=>[key,input.type==='checkbox'?input.checked:input.value])]);
    const settingsSnapshot=()=>JSON.stringify(SETTINGS.map(name=>name==='weather_enabled'?setting(name).checked:setting(name).value));
    const stationDirty=()=>!stationForm.hidden && (state.isNew || stationSnapshot()!==state.stationBaseline);
    const settingsDirty=()=>state.loaded && settingsSnapshot()!==state.settingsBaseline;
    const dirty=()=>stationDirty() || settingsDirty();
    function update() {
      const locked=state.busy || admin.isBusy() || win.DmdSession?.can_edit===false;
      $('station-fields').disabled=$('settings-fields').disabled=locked || !state.loaded;
      $('new').disabled=locked || !state.loaded;
      $('station-save').disabled=locked || !stationDirty() || !state.etag;
      $('settings-save').disabled=locked || !settingsDirty() || !state.etag;
      $('station-cancel').disabled=locked || !stationDirty();
      $('settings-cancel').disabled=locked || !settingsDirty();
      $('delete').hidden=stationForm.hidden || state.isNew;
      $('delete').disabled=locked || !state.selected || !state.etag;
      $('station-dirty').hidden=!stationDirty(); $('settings-dirty').hidden=!settingsDirty();
      $('rows').querySelectorAll('[data-weather-station]').forEach(row=>row.tabIndex=locked?-1:0);
    }
    function busy(value) {state.busy=value;$('workspace').setAttribute('aria-busy',String(value));admin.update();}
    function rows() {
      const filter=$('filter').value.trim().toLowerCase();
      $('count').textContent='('+state.items.length+')';
      $('rows').innerHTML=state.items.filter(item=>[item.ws_id,item.location].join(' ').toLowerCase().includes(filter)).map(item=>
        `<tr data-weather-station="${escape(item.ws_id)}" tabindex="0" role="button" aria-label="Edit ${escape(item.ws_id)}" aria-selected="${item.ws_id===state.selected}" class="${item.ws_id===state.selected?'is-selected':''}"><td><strong>${escape(item.ws_id)}</strong></td><td>${escape(item.location)}</td></tr>`).join('') || '<tr><td colspan="2" class="zk-muted">No matching stations. Use New station to add one.</td></tr>';
      update();
    }
    function renderDriver(type, values={}) {
      const defaults=state.driverTypes[type];
      if(!defaults)throw new Error('Driver settings are unavailable. Restart WEB and refresh.');
      const config={...defaults,...values};
      station('driver_poll_period').value=config.driver_poll_period;
      $('driver-fields').replaceChildren();driverInputs.clear();
      const choices={adc_backend:['ads1115','mock'],parity:['N','E','O'],mode:['rtu','ascii'],function_code:[3,4]};
      const integers=new Set(['slave_address','baudrate','bytesize','register_decimals','adc_channel','adc_address']);
      for(const [key,value] of Object.entries(config)) {
        if(key==='driver_poll_period')continue;
        const label=doc.createElement('label');label.className='zk-field';
        const caption=doc.createElement('span');caption.textContent=key.replaceAll('_',' ') + (key==='timeout'?' [s]':'');label.append(caption);
        const input=doc.createElement(choices[key]?'select':'input');input.className=choices[key]?'zk-select':'zk-input';
        if(choices[key]) {
          for(const choice of choices[key]){const option=doc.createElement('option');option.value=String(choice);option.textContent=String(choice);input.append(option);}
          input.value=String(value);
        }else if(typeof value==='boolean'){input.type='checkbox';input.checked=value;}
        else {input.type=typeof value==='number'||value===null?'number':'text';input.step=integers.has(key)||key.endsWith('_register')||key.endsWith('_channel')?'1':'any';input.required=value!==null;input.value=value??'';if(value===null)input.placeholder='Optional';}
        label.append(input);$('driver-fields').append(label);driverInputs.set(key,input);
      }
    }
    function driverPayload() {
      const config={driver_poll_period:numeric(station('driver_poll_period'))};
      for(const [key,input] of driverInputs)config[key]=input.type==='checkbox'?input.checked:(input.type==='number'||key==='function_code')?numeric(input,!input.required):input.value;
      return config;
    }
    function showStation(item, isNew=false) {
      state.original={...item}; state.selected=isNew?null:item.ws_id; state.isNew=isNew;
      STATION.forEach(name=>station(name).value=item[name]??'');
      station('driver_type').value=item.driver_type||'UNKNOWN';station('sim_mode').value=item.sim_mode||'off';
      renderDriver(station('driver_type').value,item.driver_config||{});
      station('ws_id').readOnly=!isNew;
      state.stationBaseline=stationSnapshot();stationForm.hidden=false;
      $('station-title').textContent=isNew?'New weather station':item.ws_id;rows();
    }
    function hideStation() {stationForm.hidden=true;state.selected=null;state.original=null;state.isNew=false;$('station-title').textContent='Choose a weather station';rows();}
    function showSettings(values) {
      state.settings={...values};
      SETTINGS.forEach(name=>name==='weather_enabled'?setting(name).checked=values[name]:setting(name).value=values[name]);
      state.settingsBaseline=settingsSnapshot();settingsForm.hidden=false;update();
    }
    function reset(profile) {
      state.profile=profile;state.loaded=false;state.items=[];state.etag=null;state.settings=null;
      settingsForm.hidden=true;hideStation();
      $('profile').textContent=profile?'Config Profile: '+profile+' · WeatherStationList.json':'No Config Profile loaded';
    }
    async function activate(profile) {
      if(profile!==state.profile)reset(profile);
      if(state.loaded){admin.message('Weather configuration for Config Profile: '+state.profile+'. Shared settings apply to every station in this profile.');return;}
      busy(true);admin.message('Loading weather configuration…');
      try {
        const result=await admin.request('weather-stations',{profile:state.profile});
        if(result.payload.profile!==state.profile)throw Object.assign(new Error('Profile changed'),{code:'profile_changed'});
        if(!Array.isArray(result.payload.weather_stations)||!result.payload.settings||!result.etag)throw new Error('WEB returned incomplete weather configuration.');
        state.items=result.payload.weather_stations;state.driverTypes=result.payload.driver_types||state.driverTypes;state.etag=result.etag;state.loaded=true;
        showSettings(result.payload.settings);
        if(state.items.length)showStation(state.items[0]);else hideStation();
        admin.connected(true);admin.message('Weather configuration loaded for Config Profile: '+state.profile+'. Select DM and RESYNC after saving.');
      } finally {busy(false);}
    }
    function numeric(input, optional=false) {
      if(optional && !input.value.trim())return null;
      const value=Number(input.value);
      if(!input.value.trim()||!Number.isFinite(value))throw new Error('Enter finite numbers in the required weather fields.');
      return value;
    }
    async function saveStation(event) {
      event.preventDefault();
      if(state.busy||admin.isBusy()||win.DmdSession?.can_edit===false||!state.loaded||!stationDirty()||!stationForm.reportValidity())return;
      let body;
      try {body={ws_id:station('ws_id').value.trim(),location:station('location').value.trim()};for(const name of STATION.slice(2))body[name]=numeric(station(name),true);body.driver_type=station('driver_type').value;body.sim_mode=station('sim_mode').value;body.driver_config=driverPayload();}
      catch(error){admin.failure(error);return;}
      busy(true);admin.message('Saving weather station…');
      try {
        const result=await admin.request(state.isNew?'weather-stations':'weather-stations/'+encodeURIComponent(state.selected),{method:state.isNew?'POST':'PATCH',body,profile:state.profile,etag:state.etag});
        const item=result.payload.weather_station;
        if(!item||!result.etag)throw new Error('Save was not confirmed. Refresh weather configuration before retrying.');
        state.etag=result.etag;
        const index=state.items.findIndex(row=>row.ws_id===item.ws_id);
        if(index<0)state.items.push(item);else state.items[index]=item;
        showStation(item);admin.connected(true);admin.message('Weather station saved to Config Profile: '+state.profile+'. RESYNC DM for definitions/thresholds and WS for the active driver/simulation.');
      }catch(error){admin.failure(error);}finally{busy(false);}
    }
    async function saveSettings(event) {
      event.preventDefault();
      if(state.busy||admin.isBusy()||win.DmdSession?.can_edit===false||!state.loaded||!settingsDirty()||!settingsForm.reportValidity())return;
      let body;
      try {body={weather_enabled:setting('weather_enabled').checked};for(const name of SETTINGS.slice(1))body[name]=numeric(setting(name));}
      catch(error){admin.failure(error);return;}
      busy(true);admin.message('Saving shared weather settings…');
      try {
        const result=await admin.request('weather-settings',{method:'PATCH',body,profile:state.profile,etag:state.etag});
        if(!result.payload.settings||!result.etag)throw new Error('Save was not confirmed. Refresh weather configuration before retrying.');
        state.etag=result.etag;showSettings(result.payload.settings);
        admin.connected(true);admin.message('Shared weather settings saved for all stations in Config Profile: '+state.profile+'. RESYNC DM for definitions/thresholds and WS for the active driver/simulation.');
      }catch(error){admin.failure(error);}finally{busy(false);}
    }
    async function remove() {
      if(state.busy||admin.isBusy()||win.DmdSession?.can_edit===false||!state.selected||!state.etag)return;
      if(!win.confirm('Delete weather station '+state.selected+' from Config Profile '+state.profile+'? Update any dish assignments referencing this station.'))return;
      busy(true);admin.message('Deleting weather station…');
      try {
        const result=await admin.request('weather-stations/'+encodeURIComponent(state.selected),{method:'DELETE',profile:state.profile,etag:state.etag});
        if(!result.etag)throw new Error('Deletion was not confirmed. Refresh weather configuration.');
        state.items=state.items.filter(item=>item.ws_id!==state.selected);state.etag=result.etag;
        if(state.items.length)showStation(state.items[0]);else hideStation();
        admin.message('Weather station deleted from Config Profile: '+state.profile+'. RESYNC DM for definitions/thresholds and WS for the active driver/simulation.');
      }catch(error){admin.failure(error);}finally{busy(false);}
    }
    function select(event) {
      const row=event.target.closest('[data-weather-station]');
      if(!row||state.busy||admin.isBusy()||(!state.isNew&&row.dataset.weatherStation===state.selected))return;
      if(stationDirty()&&!win.confirm('Discard unsaved station changes?'))return;
      const item=state.items.find(item=>item.ws_id===row.dataset.weatherStation);if(item)showStation(item);
    }
    $('rows').addEventListener('click',select);
    $('rows').addEventListener('keydown',event=>{
      if(['Enter',' '].includes(event.key)&&!event.repeat){
        event.preventDefault();select(event);
        Array.from($('rows').querySelectorAll('[data-weather-station]')).find(row=>row.dataset.weatherStation===state.selected)?.focus();
      }
    });
    $('filter').addEventListener('input',rows);
    $('new').addEventListener('click',()=>{
      if(state.busy||admin.isBusy()||!state.loaded||win.DmdSession?.can_edit===false)return;
      if(stationDirty()&&!win.confirm('Discard unsaved station changes?'))return;
      showStation({ws_id:'',location:'',latitude:null,longitude:null,elevation:null},true);station('ws_id').focus();
    });
    $('delete').addEventListener('click',remove);
    $('station-cancel').addEventListener('click',()=>{
      if(state.busy||admin.isBusy()||!stationDirty()||!win.confirm('Discard unsaved station changes?'))return;
      if(state.isNew)hideStation();else showStation(state.original);admin.update();
    });
    $('settings-cancel').addEventListener('click',()=>{
      if(!state.busy&&!admin.isBusy()&&settingsDirty()&&win.confirm('Discard unsaved monitoring changes?')){showSettings(state.settings);admin.update();}
    });
    for(const form of [stationForm,settingsForm])for(const name of ['input','change'])form.addEventListener(name,()=>admin.update());
    station('driver_type').addEventListener('change',()=>{renderDriver(station('driver_type').value);admin.update();});
    stationForm.addEventListener('submit',saveStation);settingsForm.addEventListener('submit',saveSettings);
    admin.register('weather',{reset,activate,update,dirty,isBusy:()=>state.busy});
    return {state,activate,saveStation,saveSettings,dirty,reset,driverInputs};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={init};
  if(typeof document!=='undefined')init(document,window);
})();
