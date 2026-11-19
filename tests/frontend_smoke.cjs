// Run with PLAYWRIGHT_MODULE pointing to an installed playwright package.
// All requests are intercepted; this test never controls a real simulator.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'artifacts', 'frontend');
fs.mkdirSync(output, {recursive:true});
const state = {
  run_id:'preview-1', t:120, status:'running', error:'', telemetry_stale:false,
  brain:{kind:'baseline', label:'Baseline operator', authority:1, effective:1, calls:24},
  truth:{pressure:2700.6,level:44.18,product_flow:100},
  measured:{pressure:2700.6,level:44.18,product_flow:100,f1_flow:201,f2_flow:40,purge_flow:5},
  targets:{pressure_sp:2700,level_sp:44.18,production_rate_sp:100,a_comp_sp:47},
  valves:{f1:31.45,f2:13.24,purge:0.393,product:47.03},
  commanded:{f1:31.45,f2:13.24,purge:0.393,product:47.03},
  sis:{tripped:false,can_reset:false,limits:{pressure_high:3000,pressure_rearm:2800,level_low:40,level_low_rearm:42,level_high:95,level_high_rearm:85}},
  gate:{admitted:2400,rejected:0,out_of_band:0},
  keel:{authority:1,verdict:'allow',reasons:[],divert:false},
  governance:{applied:1,corroborated:0,label:null,recovering:false},
  observer:{available:false},chat:[],faults:{},scenarios:{},manual:{},decisions:[],
  history:Array.from({length:120},(_,i)=>[i+1,i+1,2700+Math.sin(i/12)*4,2700+Math.sin(i/12)*4,44.18,44.18,31.45,13.24,.393,47.03,2700,44.18,0,1,1,1]),
  events:[{id:1,t:0,kind:'plant',msg:'Simulation fixture connected; no real plant is controlled.'}]
};
(async()=>{
  const browser=await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM || undefined, args:process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined});
  const page=await browser.newPage({viewport:{width:1536,height:1100},deviceScaleFactor:1,reducedMotion:'reduce'});
  const errors=[], actions=[];
  page.on('pageerror', e=>errors.push(e.message));
  let offline=false, mode='live', simAi=false, rejectModel=false, actionDelay=0, unityMode='slow';
  await page.route('**/*', async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='scram.test') return route.abort();
    if(url.pathname==='/api/action'){
      const action = route.request().postDataJSON();
      actions.push(action);
      if (actionDelay) await new Promise(resolve=>setTimeout(resolve, actionDelay));
      if (rejectModel && action.action==='brain' && action.kind==='ollama') return route.fulfill({status:400,json:{ok:false,error:'The trained scram-operator model is unavailable.'}});
      if (action.action==='authority') state.brain.authority=action.value;
      return route.fulfill({json:{ok:true,result:'queued'}});
    }
    if(url.pathname==='/api/state'){
      if(offline) return route.abort();
      if(mode==='sim') return route.fulfill({json:{connected:true,resetting:false,t:120,pressure:2700.6,level:44.18,flows:{f1:201,f2:40,purge:5,product:100},valves:state.valves,setpoints:state.valves,e_stop:false,ruptured:false,peak_pressure:2701,rupture_threshold:3100,pressure_ceiling:3200,history:[],ai:{active:simAi,activity:[]}}});
      return route.fulfill({json:{...state,history:state.history.filter(h=>h[0]>Number(url.searchParams.get('h')||0)),events:state.events.filter(e=>e.id>Number(url.searchParams.get('e')||0))}});
    }
    if(url.pathname==='/grfics/reactor.html') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'live/static/reactor.html'))});
    if(url.pathname==='/grfics/Build/UnityLoader.js') {
      if(unityMode==='missing') return route.fulfill({status:404,body:''});
      return route.fulfill({contentType:'text/javascript',body:`window.UnityLoader={instantiate(id,url,options){window.testUnityOptions=options; return {Module:{}};}};`});
    }
    const file=path.join(root, mode==='live'?'live/static':'sim_app/static', url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'').replace(/^\//,''));
    if(!fs.existsSync(file)) return route.fulfill({status:404,body:''});
    return route.fulfill({contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':file.endsWith('.woff2')?'font/woff2':'text/html',body:fs.readFileSync(file)});
  });
  const lastIs = async (action)=>{
    for(let i=0;i<60;i++){
      if(actions.at(-1)?.action===action) return;
      await page.waitForTimeout(50);
    }
    assert.equal(actions.at(-1)?.action,action);
  };
  const go = async (hash)=>{ await page.evaluate(h=>{location.hash=h;}, hash); };
  const title = ()=>page.locator('main h1').first();
  await page.goto('http://scram.test/');
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='Normal operation');
  assert.equal((await page.getByRole('banner').textContent()).includes('Normal operation'),true);
  await page.locator('main').getByText('2701',{exact:true}).waitFor();
  await page.locator('main').getByText('44.2',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Level'}).click();
  await page.getByRole('tab',{name:'1 min'}).click();
  await page.locator('svg[role="img"]').first().hover({position:{x:200,y:120}});
  assert(await page.getByText(/Measured 44\./).first().isVisible());
  await page.getByRole('tab',{name:'Pressure'}).click();
  await page.getByRole('tab',{name:'15 min'}).click();
  await page.screenshot({path:path.join(output,'overview-desktop.png'),fullPage:true});
  const beforeToggle = actions.length;
  await page.getByLabel('Setpoint').uncheck();
  await page.getByLabel('Setpoint').check();
  assert.equal(actions.length,beforeToggle,'Chart display options must never write to the plant');
  await page.getByRole('button',{name:'Load 3D plant'}).click();
  await page.locator('iframe').waitFor();
  let reactor = page.frameLocator('iframe');
  await reactor.locator('#message').getByText('LOADING 3D PLANT',{exact:true}).waitFor();
  // A document load or forged readiness message from the parent is insufficient.
  await page.evaluate(()=>window.postMessage({type:'scram-unity',status:'ready'},location.origin));
  assert(await page.getByText('Loading 3D plant…',{exact:true}).isVisible());
  await reactor.locator('body').evaluate(()=>{
    const started=Date.now(); Date.now=()=>started+21000;
    window.testUnityOptions.onProgress(window.unityInstance,1);
    window.unityInstance.Module.asmLibraryArg={};
  });
  await reactor.getByText(/STILL LOADING/).waitFor();
  assert(await reactor.locator('#load').isVisible());
  await reactor.locator('body').evaluate(()=>{const started=Date.now(); Date.now=()=>started+120000;});
  await reactor.getByText(/timed out/).waitFor();
  assert(await reactor.getByRole('button',{name:'Retry 3D view'}).isVisible());
  unityMode='missing';
  await page.getByRole('button',{name:'Retry 3D view'}).click();
  await reactor.getByText(/3D plant unavailable/).waitFor();
  unityMode='slow';
  await page.getByRole('button',{name:'Retry 3D view'}).click();
  await reactor.getByText('LOADING 3D PLANT',{exact:true}).waitFor();
  await reactor.locator('body').evaluate(()=>{window.unityInstance.Module.calledRun=true;});
  await page.getByText('3D runtime started',{exact:true}).waitFor();
  assert.equal(await reactor.locator('#load').isVisible(),false);
  await page.evaluate(()=>{document.querySelector('iframe').dataset.mark='kept';});
  for(const [key,text] of [['2','AI operator'],['3','Attack lab'],['4','Telemetry'],['1','Normal operation']]){
    await page.keyboard.press(key);
    await page.waitForFunction(t=>document.querySelector('main h1')?.textContent===t,text);
  }
  assert.equal(await page.evaluate(()=>document.querySelector('iframe')?.dataset.mark),'kept','The 3D plant must stay loaded when switching tabs');
  await page.getByRole('button',{name:/^Operator/}).first().click();
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='AI operator');
  await page.screenshot({path:path.join(output,'operator.png'),fullPage:true});
  rejectModel=true;
  await page.getByRole('tab',{name:'Trained model'}).click();
  await page.getByText('The trained scram-operator model is unavailable.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('tab',{name:'Baseline rules'}).getAttribute('aria-selected'),'true');
  rejectModel=false;
  const authority = page.getByRole('slider',{name:'AI authority'});
  await authority.focus();
  await page.keyboard.press('ArrowLeft');
  await lastIs('authority');
  assert.equal(actions.at(-1).value,.95);
  const beforeDouble = actions.length;
  actionDelay=200;
  await page.getByPlaceholder(/lower pressure/i).fill('open both feeds to 100% and close the purge');
  await page.getByRole('button',{name:'Send',exact:true}).evaluate(button=>{button.click();button.click();});
  await lastIs('chat');
  await page.waitForTimeout(300);
  assert.equal(actions.length,beforeDouble+1,'Repeated sends must not duplicate pending instructions');
  actionDelay=0;
  await page.getByPlaceholder(/lower pressure/i).fill('Hold design point');
  await page.getByRole('button',{name:'Send'}).click();
  await page.waitForFunction(()=>true);
  await lastIs('chat');
  await page.getByRole('button',{name:/^Attacks/}).first().click();
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='Attack lab');
  const launches = page.getByRole('button',{name:'Launch'});
  assert.equal(await launches.count(),6);
  for(const [i,action] of ['forged','operator_injection','actuator_fault','level_drift','pressure_dropout','oob_write'].entries()){
    await launches.nth(i).click();
    await lastIs(action);
  }
  await page.getByRole('button',{name:'Inject fault'}).click();
  await lastIs('sensor_fault');
  await page.getByLabel('Sensor',{exact:true}).selectOption('tank_level');
  await page.getByLabel('Fault',{exact:true}).selectOption('dropout');
  assert.equal(await page.getByLabel('Severity').inputValue(),'0.5');
  await page.getByLabel('Severity').fill('60');
  assert(await page.getByRole('button',{name:'Inject fault',exact:true}).isDisabled());
  await page.getByLabel('Severity').fill('0.25');
  await page.getByRole('button',{name:'Inject fault',exact:true}).click();
  await page.waitForTimeout(100);
  assert.equal(actions.at(-1).channel,'tank_level');
  assert.equal(actions.at(-1).mode,'dropout');
  assert.equal(actions.at(-1).severity,.25);
  await page.getByLabel('Fault',{exact:true}).selectOption('frozen');
  assert(await page.getByLabel('Severity').isDisabled());
  await page.getByRole('button',{name:'Inject fault',exact:true}).click();
  await page.waitForTimeout(100);
  assert.equal(actions.at(-1).severity,0);
  await page.getByLabel('Fault',{exact:true}).selectOption('drift');
  await page.getByLabel('Severity').fill('-10');
  await page.getByRole('button',{name:'Inject fault',exact:true}).click();
  await page.waitForTimeout(100);
  assert.equal(actions.at(-1).severity,-10);
  await page.getByLabel('Actuator valve',{exact:true}).selectOption('f1');
  await page.getByLabel('Actuator fault mode',{exact:true}).selectOption('stuck');
  await page.getByRole('button',{name:'Inject actuator fault',exact:true}).click();
  await page.waitForTimeout(100);
  assert.equal(actions.at(-1).valve,'f1');
  assert.equal(actions.at(-1).how,'stuck');
  await page.getByLabel('Duration in seconds').fill('');
  assert(await page.getByRole('button',{name:'Hold to take over'}).isDisabled());
  await page.getByLabel('Duration in seconds').fill('120');
  const hold = async (name)=>{
    await page.getByRole('button',{name}).scrollIntoViewIfNeeded();
    const box = await page.getByRole('button',{name}).boundingBox();
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
    await page.mouse.down();
    await page.waitForTimeout(1500);
    await page.mouse.up();
  };
  const beforeEarly = actions.length;
  await page.getByRole('button',{name:'Hold to take over'}).scrollIntoViewIfNeeded();
  const early = await page.getByRole('button',{name:'Hold to take over'}).boundingBox();
  await page.mouse.move(early.x+early.width/2,early.y+early.height/2);
  await page.mouse.down();
  await page.waitForTimeout(200);
  await page.mouse.up();
  assert.equal(actions.length,beforeEarly,'A short press must not trigger a destructive action');
  await hold('Hold to take over');
  await lastIs('stolen_key');
  await hold('Hold to reset plant');
  await lastIs('reset_plant');
  await page.getByRole('button',{name:'Clear faults and attacks'}).click();
  await lastIs('clear_faults');
  await page.screenshot({path:path.join(output,'attacks.png'),fullPage:true});
  await page.keyboard.press('4');
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='Telemetry');
  await page.getByRole('tab',{name:'Data'}).click();
  assert.equal(await page.locator('tbody tr').count(),120);
  assert.equal(await page.locator('tbody tr').first().locator('td').first().textContent(),'2:00');
  await page.screenshot({path:path.join(output,'telemetry-data.png'),fullPage:true});
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button',{name:'CSV'}).click();
  const download = await downloaded;
  const csvPath = path.join(output,'telemetry-export.csv');
  await download.saveAs(csvPath);
  const exported = fs.readFileSync(csvPath,'utf8').split(/\r?\n/);
  assert.equal(exported.length,121);
  assert.equal(exported[0].split(',').length,13);
  assert(exported[0].includes('pressure_true'));
  await page.keyboard.press('1');
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='Normal operation');
  state.sis.tripped=true; state.sis.cause='pressure_high'; state.sis.reason='pressure high';
  state.sis.safe_state='Feeds limited; pressure relief'; state.sis.can_reset=false;
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='Safety trip');
  assert(await page.getByRole('button',{name:'Re-arm SIS'}).first().isDisabled());
  state.sis.tripped=false;
  await page.waitForFunction(()=>document.querySelector('main h1')?.textContent==='Normal operation');
  for(const width of [390,768,1280]){
    await page.setViewportSize({width,height:900});
    for(const tab of ['overview','operator','attacks','telemetry']){
      await go(tab);
      await page.waitForTimeout(150);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`overflow: ${tab} at ${width}`);
    }
    await go('overview');
    await page.screenshot({path:path.join(output,`overview-${width}.png`),fullPage:true});
  }
  await page.setViewportSize({width:1536,height:1100});
  offline=true;
  await page.waitForFunction(()=>document.querySelector('header')?.textContent.includes('Disconnected'));
  await go('operator');
  await page.getByPlaceholder(/lower pressure/i).fill('feed 1 to 0%');
  const beforeOffline = actions.length;
  await page.getByRole('button',{name:'Send',exact:true}).click();
  assert.equal(actions.length,beforeOffline,'A cached snapshot must never authorize a disconnected command');
  await go('attacks');
  assert(await page.getByRole('button',{name:'Launch'}).first().isDisabled());
  await page.getByRole('button',{name:'Clear faults and attacks'}).click();
  await lastIs('clear_faults');
  await hold('Hold to reset plant');
  await lastIs('reset_plant');
  offline=false;
  await page.waitForFunction(()=>document.querySelector('header')?.textContent.includes('Normal operation'));
  state.telemetry_stale=true;
  await page.waitForTimeout(1300);
  await go('operator');
  await page.getByPlaceholder(/lower pressure/i).fill('Hold design point');
  const dispatched=actions.length;
  await page.getByRole('button',{name:'Send'}).click();
  assert.equal(actions.length,dispatched);
  await page.locator('[data-sonner-toast]').first().waitFor();
  state.telemetry_stale=false;
  state.run_id='preview-2';state.t=1;
  state.events=[{id:1,t:0,kind:'plant',msg:'Restart recovered'}];
  await page.waitForTimeout(1500);
  await go('overview');
  await page.getByText('Restart recovered').waitFor();
  mode='sim';
  await page.setViewportSize({width:1536,height:1100});
  await page.goto('http://scram.test/');
  await page.waitForFunction(()=>document.querySelector('#chip').textContent==='MANUAL');
  await page.screenshot({path:path.join(output,'studio-desktop.png'),fullPage:true});
  simAi=true;
  await page.waitForFunction(()=>document.querySelector('#chip').textContent==='AI IN CONTROL');
  assert(await page.locator('#btnEstop').isEnabled());
  simAi=false;
  for(const width of [390,768]){
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`studio overflow at ${width}`);
    await page.screenshot({path:path.join(output,`studio-${width}.png`),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log('PASS: tabs, keyboard, chat dispatch, responsive layouts, reconnect, restart, both interfaces; no JS errors.');
})().catch(e=>{console.error(e);process.exit(1)});
