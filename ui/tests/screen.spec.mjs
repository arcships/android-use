import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

const raw=await readFile('../src/android_use/assets/phone-screen.html','utf8');
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4xkAAAAASUVORK5CYII=';
const sample=()=>({ok:true,stream_id:'one',seq:1,frame:{seq:1,data:png,mimeType:'image/png',width:540,height:1200},
  frame_available:true,paused:false,pause_reason:null,connected:true,capturing:true,
  device:{model:'Test Android'},viewport:[1080,2400],error:null});

async function standalone(page){
  let state=sample(),calls=[];
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{write:async(items)=>{
    const png=await items[0].getType('image/png');const bytes=new Uint8Array(await png.arrayBuffer());
    window.copiedImage={type:png.type,signature:[...bytes.slice(0,8)]};
  }}}));
  await page.route('https://preview.test/**',route=>{
    const request=route.request();
    if(request.method()==='GET')return route.fulfill({contentType:'text/html',body:raw.replace('<!--__MODE__-->','<script>window.__ANDROID_USE_STANDALONE__=true</script>')});
    const args=request.postDataJSON();calls.push({url:request.url(),args});
    return route.fulfill({contentType:'application/json',body:JSON.stringify(request.url().endsWith('/action')?
      {ok:true,saved_path:'/tmp/saved.jpg'}:state)});
  });
  await page.goto('https://preview.test/token/');
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  return {set:value=>{state=value},calls};
}

test('renders live device and maps pointer to Android pixels',async({page})=>{
  const {calls}=await standalone(page);
  await expect(page.locator('#model')).toHaveText('Test Android');
  await page.locator('#frame').click({position:{x:50,y:100}});
  await expect.poll(()=>calls.some(c=>c.args.action==='tap')).toBeTruthy();
  const click=calls.find(c=>c.args.action==='tap');
  expect(click.args.x).toBeGreaterThan(0);expect(click.args.x).toBeLessThan(1080);
  expect(click.args.y).toBeGreaterThan(0);expect(click.args.y).toBeLessThan(2400);
});
test('pause removes previously decoded secret frame and disables actions',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),paused:true,pause_reason:'authentication',frame:null,frame_available:false});
  await expect(page.locator('#app')).toHaveAttribute('data-state','paused');
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
  await expect(page.locator('#home')).toBeDisabled();
  await expect(page.locator('#copy')).toBeDisabled();
  api.set({...sample(),seq:2,frame:{...sample().frame,seq:2}});
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
});
test('toolbar copies the displayed frame as a PNG image without saving a file',async({page})=>{
  const {calls}=await standalone(page);
  await page.locator('#home').click();
  await expect.poll(()=>calls.some(c=>c.args.action==='home')).toBeTruthy();
  await page.locator('#copy').click();
  await expect(page.locator('#notice')).toHaveText('画面已复制');
  expect(await page.evaluate(()=>window.copiedImage)).toEqual({type:'image/png',signature:[137,80,78,71,13,10,26,10]});
  expect(calls.some(c=>c.args.action==='save')).toBeFalsy();
});
test('disconnection shows offline and refresh reconnects',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),connected:false,frame:null,frame_available:false});
  await expect(page.locator('#app')).toHaveAttribute('data-state','offline');
  api.set({...sample(),seq:2,frame:{...sample().frame,seq:2}});
  await page.locator('#refresh').click();
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
});
test('clipboard denial reports failure without claiming success',async({page})=>{
  const {calls}=await standalone(page);
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{write:async()=>{throw new DOMException('Denied','NotAllowedError');}}}));
  await page.locator('#copy').click();
  await expect(page.locator('#notice')).toHaveText('复制失败，请允许写入剪贴板');
  expect(calls.some(c=>c.args.action==='save')).toBeFalsy();
});
test('native MCP App handshakes and uses app-only tools without HTTP polling',async({page})=>{
  await page.route('http://native.test/**',route=>route.fulfill({contentType:'text/html',body:raw}));
  const host=`<iframe src="http://native.test/widget" style="width:380px;height:820px;border:0"></iframe>
    <script>window.calls=[];window.addEventListener('message',e=>{const m=e.data;if(!m||m.id===undefined)return;
    window.calls.push(m);let result={};
    if(m.method==='ui/initialize')result={protocolVersion:m.params.protocolVersion,hostInfo:{name:'Test host',version:'1'},hostCapabilities:{serverTools:{}},hostContext:{displayMode:'fullscreen'}};
    if(m.method==='ui/request-display-mode')result={mode:'fullscreen'};
    if(m.method==='tools/call')result={content:[],structuredContent:${JSON.stringify(sample())}};
    e.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*');});</script>`;
  await page.route('http://host.test/**',route=>route.fulfill({contentType:'text/html',body:host}));
  await page.goto('http://host.test/');
  const widget=page.frameLocator('iframe');
  await expect(widget.locator('#app')).toHaveAttribute('data-state','live');
  const names=await page.evaluate(()=>window.calls.filter(c=>c.method==='tools/call').map(c=>c.params.name));
  expect(names).toContain('aua_screen_frame');
});
