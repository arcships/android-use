// Opt-in real-device UI acceptance; no messages, purchases, or settings mutations.
import { chromium } from '@playwright/test';
import { execFileSync, spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const root=resolve('..'),serial=process.env.ANDROID_SERIAL;
if(!serial)throw new Error('Set ANDROID_SERIAL for the intended device');
function phone(op,args={}){
  return JSON.parse(execFileSync('uv',['run','--frozen','--directory',root,'android-use','--serial',serial,op,JSON.stringify(args)],{encoding:'utf8'}));
}
const opened=phone('screen');if(!opened.ok)throw new Error(JSON.stringify(opened));
const originalApp=phone('observe',{mode:'tree',max_nodes:1}).foreground;
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:390,height:844},permissions:['clipboard-read','clipboard-write']});
const page=await context.newPage();
page.on('pageerror',error=>console.error('UI error:',error.message));
try{
  await page.goto(opened.url);
  await page.waitForFunction(()=>document.querySelector('#app').dataset.state==='live',{},{timeout:20000});
  const first=await page.locator('#app').getAttribute('data-frame-seq');
  await page.waitForFunction(seq=>+document.querySelector('#app').dataset.frameSeq>+seq,first,{timeout:10000});
  const screenshot=process.env.ANDROID_USE_PREVIEW_SCREENSHOT||'/tmp/android-use-live-sidebar.png';
  await page.screenshot({path:screenshot});
  await page.locator('#copy').click();
  await page.waitForFunction(()=>document.querySelector('#notice').textContent==='画面已复制');
  const clipboardTypes=await page.evaluate(async()=>{const items=await navigator.clipboard.read();return items.flatMap(item=>item.types);});
  if(!clipboardTypes.includes('image/png'))throw new Error('Clipboard does not contain a PNG image');
  const pause=phone('pause');if(!pause.ok)throw new Error(JSON.stringify(pause));
  await page.waitForFunction(()=>document.querySelector('#app').dataset.state==='paused');
  if(await page.locator('#frame').getAttribute('src'))throw new Error('Paused preview retained sensitive pixels');
  const pids=spawnSync('adb',['-s',serial,'shell','pidof screenrecord'],{encoding:'utf8'}).stdout.trim();
  if(pids)throw new Error('Capture did not stop during authentication');
  const resumed=phone('resume');if(!resumed.ok)throw new Error(JSON.stringify(resumed));
  await page.waitForFunction(()=>document.querySelector('#app').dataset.state==='live',{},{timeout:20000});
  // Home is harmless; video must show a new decoded frame afterward.
  const previous=await page.locator('#frame').getAttribute('src');
  await page.locator('#home').click();
  await page.waitForFunction(old=>document.querySelector('#frame').src!==old,previous,{timeout:10000});
  if(originalApp)phone('launch_app',{package:originalApp});
  console.log(JSON.stringify({live:true,sequence_advances:true,clipboard_image:true,pause_stops_device_capture:true,
    resume:true,home_updates_video:true,screenshot}));
}catch(error){
  console.error('UI state:',await page.locator('#app').getAttribute('data-state'),await page.locator('#overlay').innerText());
  const debug=await page.evaluate(async()=>{const r=await fetch('api/frame',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();delete d.frame;return d;});
  console.error('Preview status:',JSON.stringify(debug));throw error;
}finally{phone('resume');await browser.close();}
