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
  run_id:'preview-1', t:120, status:'running', error:'',
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
  const browser=await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM || undefined});
  const page=await browser.newPage({viewport:{width:1536,height:1100},deviceScaleFactor:1,reducedMotion:'reduce'});
  const errors=[], actions=[];
  page.on('pageerror', e=>errors.push(e.message));
  let offline=false, mode='live', simAi=false;
  await page.route('**/*', async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='scram.test') return route.abort();
    if(url.pathname==='/api/action'){
      actions.push(route.request().postDataJSON());
      return route.fulfill({json:{ok:true,result:'queued'}});
    }
    if(url.pathname==='/api/state'){
      if(offline) return route.abort();
      if(mode==='sim') return route.fulfill({json:{connected:true,resetting:false,t:120,pressure:2700.6,level:44.18,flows:{f1:201,f2:40,purge:5,product:100},valves:state.valves,setpoints:state.valves,e_stop:false,ruptured:false,peak_pressure:2701,rupture_threshold:3100,pressure_ceiling:3200,history:[],ai:{active:simAi,activity:[]}}});
      return route.fulfill({json:{...state,history:state.history.filter(h=>h[0]>Number(url.searchParams.get('h')||0)),events:state.events.filter(e=>e.id>Number(url.searchParams.get('e')||0))}});
    }
    if(url.pathname==='/grfics/reactor.html') return route.fulfill({contentType:'text/html',body:'<body style="margin:0;background:#0c1115;color:#99a9b4;display:grid;place-items:center;height:100vh;font:13px system-ui"><div style="text-align:center"><div style="font-size:60px;color:#a5eed2">⬡</div><p>GRFICS reactor viewport</p><p style="font-size:11px">3D simulation is unavailable in this isolated UI test</p></div></body>'});
    const file=path.join(root, mode==='live'?'live/static':'sim_app/static', url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'').replace(/^\//,''));
    if(!fs.existsSync(file)) return route.fulfill({status:404,body:''});
    return route.fulfill({contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'text/html',body:fs.readFileSync(file)});
  });
  await page.goto('http://scram.test/');
  await page.waitForFunction(()=>document.querySelector('#connTxt').textContent==='live');
  assert.equal(await page.locator('#summaryPressure').textContent(),'2700.6');
  assert.equal(await page.locator('#summaryLevel').textContent(),'44.18');
  await page.click('[data-series="level"]');
  assert.equal(await page.locator('#trendValue').textContent(),'44.18');
  assert.equal(await page.locator('#trendUnit').textContent(),'%');
  await page.locator('#cOverview').hover({position:{x:120,y:80}});
  assert(await page.locator('.overview-tooltip').isVisible());
  assert((await page.locator('.overview-tooltip').textContent()).includes('AI sensor: 44.18 %'));
  await page.click('[data-series="pressure"]');
  await page.click('.window-switch [data-win="120"]');
  assert.equal(await page.locator('[data-view="analysis"] [data-win="120"]').getAttribute('aria-pressed'),'true');
  await page.click('.window-switch [data-win="600"]');
  await page.locator('.instrument-details summary').focus();
  await page.keyboard.press('Enter');
  assert(await page.locator('#dial').isVisible());
  await page.keyboard.press('Enter');
  await page.locator('#pageTitle').click();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(output,'overview-desktop.png'),fullPage:true});
  for(const tab of ['console','analysis','overview']){
    await page.click(`[data-tab="${tab}"]`);
    assert.equal(await page.locator(`[data-tab="${tab}"]`).getAttribute('aria-selected'),'true');
    await page.screenshot({path:path.join(output,`${tab}.png`),fullPage:true});
  }
  await page.click('[data-tab="analysis"]');
  const beforeInspect = actions.length;
  await page.click('[data-inspect="level"]');
  assert.equal(await page.locator('#inspectorSignal').inputValue(),'level');
  assert.equal(await page.locator('#inspectorValue').textContent(),'44.18');
  await page.locator('#showSetpoints').uncheck();
  await page.locator('#showLimits').uncheck();
  assert.equal(actions.length,beforeInspect,'Chart display options must never write to the plant');
  await page.locator('#showSetpoints').check();
  await page.locator('#showLimits').check();
  await page.click('[data-explorer="data"]');
  assert.equal(await page.locator('#telemetryRows tr').count(),120);
  assert.equal(await page.locator('#telemetryRows tr').first().locator('td').first().textContent(),'2:00');
  await page.screenshot({path:path.join(output,'telemetry-data.png'),fullPage:true});
  const downloaded = page.waitForEvent('download');
  await page.click('#exportTelemetry');
  const download = await downloaded;
  const csvPath = path.join(output,'telemetry-export.csv');
  await download.saveAs(csvPath);
  const exported = fs.readFileSync(csvPath,'utf8').split(/\r?\n/);
  assert.equal(exported.length,121);
  assert.equal(exported[0].split(',').length,16);
  assert(exported[0].includes('true_pressure_kpa'));
  await page.locator('[data-explorer="data"]').focus();
  await page.keyboard.press('ArrowLeft');
  assert.equal(await page.locator('[data-explorer="charts"]').getAttribute('aria-selected'),'true');
  assert.equal(await page.locator('[data-tab="analysis"]').getAttribute('aria-selected'),'true');
  await page.click('[data-inspect="pressure"]');
  await page.click('[data-tab="overview"]');
  await page.locator('[data-tab="overview"]').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('[data-tab="console"]').getAttribute('aria-selected'),'true');
  await page.fill('#say','Hold design point');
  await page.locator('#compose button').click();
  assert.equal(actions.at(-1).action,'chat');
  for(const [tab,button,action] of [['sensor','goSensor','sensor_fault'],['valve','goValve','actuator_fault'],['stolen','goStolen','stolen_key'],['forged','goForged','forged'],['oob','goOob','oob_write']]){
    await page.click(`[data-atk="${tab}"]`);
    await page.click('#'+button);
    assert.equal(actions.at(-1).action,action);
  }
  state.sis.tripped=true; state.sis.cause='pressure_high'; state.sis.reason='pressure high';
  state.sis.safe_state='Feeds limited; pressure relief'; state.sis.can_reset=false;
  await page.waitForFunction(()=>document.querySelector('#sisPill').textContent==='SIS SAFE-HOLD');
  assert(await page.locator('#btnReset2').isDisabled());
  state.sis.tripped=false;
  await page.waitForFunction(()=>document.querySelector('#sisPill').textContent==='SIS ARMED');
  for(const width of [390,768,1280]){
    await page.setViewportSize({width,height:900});
    for(const tab of ['overview','console','analysis']){
      await page.click(`[data-tab="${tab}"]`);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`overflow: ${tab} at ${width}`);
    }
    await page.click('[data-explorer="data"]');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`data explorer overflow at ${width}`);
    await page.click('[data-explorer="charts"]');
    await page.click('[data-tab="overview"]');
    assert((await page.locator('.viewport').boundingBox()).height>=250,`plant viewport collapsed at ${width}`);
    await page.screenshot({path:path.join(output,`overview-${width}.png`),fullPage:true});
  }
  offline=true;
  await page.waitForFunction(()=>document.querySelector('#connTxt').textContent==='offline');
  assert.equal(await page.locator('#sisPill').textContent(),'SIS UNKNOWN');
  offline=false;
  await page.waitForFunction(()=>document.querySelector('#connTxt').textContent==='live');
  assert(await page.locator('#banner').isHidden());
  state.telemetry_stale=true;
  await page.waitForFunction(()=>document.querySelector('#connTxt').textContent==='stale telemetry');
  await page.click('[data-tab="console"]');
  await page.fill('#say','Hold design point');
  const dispatched=actions.length;
  await page.locator('#compose button').click();
  assert.equal(actions.length,dispatched);
  state.telemetry_stale=false;
  await page.waitForFunction(()=>document.querySelector('#connTxt').textContent==='live');
  state.run_id='preview-2';state.t=1;
  state.events=[{id:1,t:0,kind:'plant',msg:'Restart recovered'}];
  await page.waitForTimeout(1200);
  await page.click('[data-tab="analysis"]');
  assert((await page.locator('#log').textContent()).includes('Restart recovered'));
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
