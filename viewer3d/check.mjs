// Actual Chromium/WebGL check of the generated Pages site, run on Actions.
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
const require=createRequire(resolve('build/viewer-tools/package.json'));
const {chromium}=require('playwright');
const server=spawn('python3',['-m','http.server','8765','--bind','127.0.0.1','--directory','build/pages'],{stdio:'ignore'});
let browser,page;
const results=[],errors=[],external=[];
const telemetry=()=>page.evaluate(()=>({canvas:{...document.querySelector('canvas').dataset},status:document.querySelector('#status').textContent,
  layers:[...document.querySelectorAll('#layers input')].map(e=>({id:e.id,checked:e.checked}))}));
try{
  for(let i=0;i<30;i++){try{if((await fetch('http://127.0.0.1:8765/gds/catalog.json')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
  page=await browser.newPage({viewport:{width:1200,height:850}});
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:8765/')&&!r.url().startsWith('blob:')&&!r.url().startsWith('data:'))external.push(r.url());});
  const catalog=JSON.parse(await readFile('build/pages/gds/catalog.json','utf8'));
  for(const name of Object.keys(catalog.designs)){
    await page.goto('http://127.0.0.1:8765/gds/'+name+'/',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('canvas').dataset.ready==='true'||document.querySelector('#status').dataset.error==='true',{},{timeout:180000});
    assert.equal(await page.locator('#status').getAttribute('data-error'),null);
    assert.ok(Number(await page.locator('canvas').getAttribute('data-loaded'))>=3);
    await page.locator('#v-top').click();await page.locator('#zs').evaluate(e=>{e.value='16';});
    await page.locator('#zs').dispatchEvent('input');assert.equal(await page.locator('#zv').textContent(),'16×');
    const box=await page.locator('canvas').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
    await page.locator('#tip').waitFor({state:'visible'});assert.match(await page.locator('#tip').textContent(),/µm/);
    const cb=page.locator('#ly-poly');if(await cb.count()){
      const count=Number(await page.locator('canvas').getAttribute('data-loaded'));
      await cb.check();await page.waitForFunction(n=>Number(document.querySelector('canvas').dataset.loaded)>n,count,{timeout:180000});
      await page.screenshot({path:'build/viewer/'+name+'-poly.png'});
      // Regression: this click timed out with the original continuous RAF loop.
      // Keep the same click check; do not bypass it with force/evaluate.
      const before=performance.now();
      await cb.uncheck();assert.equal(Number(await page.locator('canvas').getAttribute('data-loaded')),count);
      results.push({design:name,poly_toggle_ms:performance.now()-before,after_poly:await telemetry()});
    }
    await page.waitForTimeout(750);
    const frames=Number(await page.locator('canvas').getAttribute('data-frames'));
    await page.waitForTimeout(500);
    assert.equal(Number(await page.locator('canvas').getAttribute('data-frames')),frames,'static layout keeps redrawing');
    await page.screenshot({path:'build/viewer/'+name+'-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.waitForTimeout(150);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.ok((await page.locator('canvas').boundingBox()).height>200);
    await page.screenshot({path:'build/viewer/'+name+'-mobile.png'});
    results.push({design:name,loaded_layers:Number(await page.locator('canvas').getAttribute('data-loaded')),mobile_width:390});
    await page.setViewportSize({width:1200,height:850});
  }
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await writeFile('build/viewer/browser.json',JSON.stringify({status:'pass',results,errors,external},null,2)+'\n');
  console.log('Self-hosted WebGL, controls, hover and 390px layout pass',results);
}catch(e){
  let current;
  if(page){try{current=await telemetry();await page.screenshot({path:'build/viewer/browser-failure.png',timeout:15000});}catch(c){current={diagnostic_error:String(c)};}}
  await writeFile('build/viewer/browser.json',JSON.stringify({status:'fail',failure:String(e),results,errors,external,current},null,2)+'\n');
  throw e;
}finally{if(browser)await browser.close();server.kill();}
