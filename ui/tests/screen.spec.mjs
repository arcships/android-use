import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

const raw=await readFile('../src/android_use/assets/phone-screen.html','utf8');
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4xkAAAAASUVORK5CYII=';
const sample=()=>({ok:true,stream_id:'one',seq:1,frame:{seq:1,data:png,mimeType:'image/png',width:540,height:1200},
  frame_available:true,paused:false,pause_reason:null,connected:true,capturing:true,
  device:{model:'Test Android'},viewport:[1080,2400],error:null});
const jevSample=()=>({ok:true,enabled:false,configured:false,has_saved_key:false,key_source:'none',model:'jev-1.14.0',base_url:'https://api.typesafe.ai/v1'});

async function standalone(page,initial=sample()){
  let state=initial,calls=[],jev=jevSample(),settingsFailure=null,frameFailure=false,actionFailure=false;
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{write:async(items)=>{
    const png=await items[0].getType('image/png');const bytes=new Uint8Array(await png.arrayBuffer());
    window.copiedImage={type:png.type,signature:[...bytes.slice(0,8)]};
  }}}));
  await page.route('https://preview.test/**',route=>{
    const request=route.request();
    if(request.method()==='GET')return route.fulfill({contentType:'text/html',body:raw.replace('<!--__MODE__-->','<script>window.__ANDROID_USE_STANDALONE__=true</script>')});
    const args=request.postDataJSON();calls.push({url:request.url(),args});
    if(request.url().endsWith('/frame')&&frameFailure)return route.abort('connectionreset');
    if(args.action?.startsWith('jev_')){
      if(args.action==='jev_save'&&settingsFailure)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify(settingsFailure)});
      if(args.action==='jev_save')jev={...jev,enabled:true,configured:true,has_saved_key:true,key_source:'local'};
      if(args.action==='jev_disable')jev={...jev,enabled:false};
      if(args.action==='jev_clear')jev=jevSample();
      if(args.action==='jev_images_on'||args.action==='jev_images_off')jev={...jev,images_enabled:args.action==='jev_images_on'};
      return route.fulfill({contentType:'application/json',body:JSON.stringify({...jev,...(['jev_save','jev_test'].includes(args.action)?{tested:true,latency_ms:120}:{})})});
    }
    if(request.url().endsWith('/action')&&actionFailure)return route.fulfill({status:503,body:'Unavailable'});
    return route.fulfill({contentType:'application/json',body:JSON.stringify(request.url().endsWith('/action')?
      {ok:true,saved_path:'/tmp/saved.jpg'}:state)});
  });
  await page.goto('https://preview.test/token/');
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  return {set:value=>{state=value},calls,failSettings:value=>{settingsFailure=value},failFrames:value=>{frameFailure=value},failActions:value=>{actionFailure=value}};
}

test('short video recovery preserves the picture without a blocking overlay',async({page})=>{
  const api=await standalone(page);
  const source=await page.locator('#frame').getAttribute('src');
  api.set({...sample(),error:'video_stream_ended',capturing:false,frame:null});
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  await expect(page.locator('#overlay')).toBeHidden();
  await expect(page.locator('#frame')).toHaveAttribute('src',source);
  await expect(page.locator('#home')).toBeDisabled();
  // A replacement encoder starting with the old cache is still buffering.
  api.set(sample());
  await expect.poll(()=>api.calls.filter(c=>c.url.endsWith('/frame')).length).toBeGreaterThan(3);
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  api.set({...sample(),seq:2,frame:{...sample().frame,seq:2}});
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  await expect(page.locator('#home')).toBeEnabled();
  expect(api.calls.some(c=>c.url.endsWith('/refresh'))).toBeFalsy();
});
test('refresh reports success only after a newly decoded picture arrives',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),frame:null,frame_available:false,capturing:false});
  await page.locator('#refresh').click();
  await expect(page.locator('#notice')).toHaveText('正在重连…');
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  api.set(sample()); // A successful HTTP response with the old cache is insufficient.
  await expect.poll(()=>api.calls.filter(c=>c.url.endsWith('/frame')).length).toBeGreaterThan(3);
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  await expect(page.locator('#notice')).not.toHaveText('已重新连接');
  api.set({...sample(),seq:2,frame:{...sample().frame,seq:2}});
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  await expect(page.locator('#notice')).toHaveText('已重新连接');
});
test('refresh cannot claim a disconnected phone is connected',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),connected:false,frame:null,frame_available:false});
  await expect(page.locator('#app')).toHaveAttribute('data-state','offline');
  await page.locator('#refresh').click();
  await expect(page.locator('#app')).toHaveAttribute('data-state','offline');
  await expect(page.locator('#notice')).not.toHaveText('已重新连接');
});
test('encoder restart or rotation holds the picture until fresh geometry arrives',async({page})=>{
  const api=await standalone(page);
  const source=await page.locator('#frame').getAttribute('src');
  api.set({...sample(),frame_available:false,frame:null,viewport:[2400,1080]});
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  await expect(page.locator('#overlay')).toBeHidden();
  await expect(page.locator('#frame')).toHaveAttribute('src',source);
  await expect(page.locator('#copy')).toBeDisabled();
  api.set({...sample(),seq:2,viewport:[2400,1080],frame:{...sample().frame,seq:2,width:1200,height:540}});
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  await expect(page.locator('#app')).toHaveAttribute('data-orientation','landscape');
});
test('a transient frame request failure clears the frame and automatically recovers',async({page})=>{
  const api=await standalone(page);
  api.failFrames(true);
  await expect(page.locator('#app')).toHaveAttribute('data-state','connecting');
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
  await expect(page.locator('#copy')).toBeDisabled();
  api.failFrames(false);
  // The same cached sequence must be fetched again after removing its image.
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  await expect(page.locator('#frame')).toHaveAttribute('src',/^data:/);
});
test('display metadata failure keeps updating pictures without enabling stale coordinates',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),interaction_ready:false,seq:2,frame:{...sample().frame,seq:2}});
  await expect(page.locator('#app')).toHaveAttribute('data-frame-seq','2');
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  await expect(page.locator('#overlay')).toBeHidden();
  await expect(page.locator('#home')).toBeDisabled();
  await page.locator('#frame').click();
  expect(api.calls.some(c=>c.args.action==='tap')).toBeFalsy();
  api.set({...sample(),interaction_ready:true,seq:2,frame:null});
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  await expect(page.locator('#home')).toBeEnabled();
});
test('persistent transport failure becomes offline but keeps retrying',async({page})=>{
  const api=await standalone(page);
  api.failFrames(true);
  await expect(page.locator('#app')).toHaveAttribute('data-state','connecting');
  await expect(page.locator('#app')).toHaveAttribute('data-state','offline',{timeout:8000});
  await expect(page.locator('#overlay-detail')).toContainText('正在自动重试');
  await expect(page.locator('#overlay-detail')).toContainText('重新打开手机屏幕');
  api.failFrames(false);
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
});
test('a stalled encoder cannot keep the cached frame live indefinitely',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),error:'video_stream_ended',frame:null});
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  // Clearing the error without a higher sequence still does not prove recovery.
  api.set(sample());
  await expect(page.locator('#app')).toHaveAttribute('data-state','offline',{timeout:8000});
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
  api.set({...sample(),seq:2,frame:{...sample().frame,seq:2}});
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
});
test('a device without screenrecord explains the limit instead of retrying',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),error:'preview_unsupported_device',frame:null,frame_available:false,capturing:false});
  // The phone is connected; only the video capability is missing.
  await expect(page.locator('#app')).toHaveAttribute('data-state','unsupported');
  await expect(page.locator('#status')).toContainText('不支持预览');
  await expect(page.locator('#status')).not.toContainText('未连接');
  await expect(page.locator('#overlay-text')).toHaveText('此手机不支持实时预览');
  await expect(page.locator('#overlay-detail')).toContainText('screenrecord');
  await expect(page.locator('#overlay-detail')).not.toContainText('重试');
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
  await expect(page.locator('#home')).toBeDisabled();
});
test('user can end an authentication pause from the panel but not a device lock',async({page})=>{
  const api=await standalone(page);
  await expect(page.locator('#resume')).toBeHidden();
  api.set({...sample(),paused:true,pause_reason:'device_locked',frame:null,frame_available:false});
  await expect(page.locator('#app')).toHaveAttribute('data-state','paused');
  await expect(page.locator('#resume')).toBeHidden();
  api.set({...sample(),paused:true,pause_reason:'authentication',frame:null,frame_available:false});
  await expect(page.locator('#resume')).toBeVisible();
  api.set(sample());
  await page.locator('#resume').click();
  await expect(page.locator('#app')).toHaveAttribute('data-state','live');
  await expect(page.locator('#resume')).toBeHidden();
  expect(api.calls.filter(call=>call.args.action==='resume')).toHaveLength(1);
});
test('authentication during video recovery keeps the frame cleared',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),error:'video_stream_ended',frame:null});
  await expect(page.locator('#app')).toHaveAttribute('data-state','buffering');
  api.set({...sample(),paused:true,pause_reason:'authentication',frame:null,frame_available:false});
  await expect(page.locator('#app')).toHaveAttribute('data-state','paused');
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
  await expect(page.locator('#home')).toBeDisabled();
});

test('renders live device and maps pointer to Android pixels',async({page})=>{
  const {calls}=await standalone(page);
  await expect(page.locator('#model')).toHaveText('Test Android');
  await page.locator('#frame').click({position:{x:50,y:100}});
  await expect.poll(()=>calls.some(c=>c.args.action==='tap')).toBeTruthy();
  const click=calls.find(c=>c.args.action==='tap');
  expect(click.args.x).toBeGreaterThan(0);expect(click.args.x).toBeLessThan(1080);
  expect(click.args.y).toBeGreaterThan(0);expect(click.args.y).toBeLessThan(2400);
});
test('ordinary actions stay quiet on success and failure without retrying',async({page})=>{
  const api=await standalone(page);
  for(const fail of [false,true]){
    api.failActions(fail);
    for(const action of ['home','back','tap','swipe']){
      const before=api.calls.filter(c=>c.args.action===action).length;
      if(action==='home'||action==='back')await page.locator('#'+action).click();
      else if(action==='tap')await page.locator('#frame').click({position:{x:50,y:100}});
      else{
        const box=await page.locator('#frame').boundingBox();
        await page.mouse.move(box.x+50,box.y+160);await page.mouse.down();
        await page.mouse.move(box.x+50,box.y+60);await page.mouse.up();
      }
      await expect(page.locator('#home')).toBeEnabled();
      expect(api.calls.filter(c=>c.args.action===action)).toHaveLength(before+1);
      await expect(page.locator('#notice')).toBeEmpty();
    }
  }
});
for(const viewport of [{width:390,height:844},{width:280,height:600},{width:720,height:420}]){
  test(`phone shell preserves screen coordinates and fits ${viewport.width}x${viewport.height}`,async({page})=>{
    await page.setViewportSize(viewport);
    if(viewport.width===280)await page.emulateMedia({colorScheme:'dark'});
    const pixels=(width,height)=>Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#487e66"/></svg>`).toString('base64');
    const state={...sample(),frame:{...sample().frame,data:pixels(540,1200),mimeType:'image/svg+xml'}};
    const api=await standalone(page,state);
    for(const [seq,width,height] of [[1,1080,2400],[2,2400,1080]]){
      if(seq===2)api.set({...state,seq,viewport:[width,height],frame:{...state.frame,seq,width:1200,height:540,data:pixels(1200,540)}});
      await expect(page.locator('#app')).toHaveAttribute('data-frame-seq',String(seq));
      const screen=await page.locator('#frame').boundingBox();
      const shell=await page.locator('#phone-shell').boundingBox();
      expect(Math.abs(screen.width/screen.height-width/height)).toBeLessThan(.002);
      expect(shell.width-screen.width).toBeGreaterThan(12);
      expect(shell.height-screen.height).toBeGreaterThan(12);
      for(const box of [shell,await page.locator('footer').boundingBox()]){
        expect(box.x).toBeGreaterThanOrEqual(0);expect(box.y).toBeGreaterThanOrEqual(0);
        expect(box.x+box.width).toBeLessThanOrEqual(viewport.width+.5);
        expect(box.y+box.height).toBeLessThanOrEqual(viewport.height+.5);
      }
      const before=api.calls.filter(c=>c.args.action==='tap').length;
      await page.locator('#phone-shell').click({position:{x:3,y:shell.height/2}});
      await page.mouse.click(screen.x+screen.width*.25,screen.y+screen.height*.75);
      await expect.poll(()=>api.calls.filter(c=>c.args.action==='tap').length).toBe(before+1);
      const click=api.calls.filter(c=>c.args.action==='tap').at(-1).args;
      expect(Math.abs(click.x-width*.25)).toBeLessThanOrEqual(1);
      expect(Math.abs(click.y-height*.75)).toBeLessThanOrEqual(1);
      await expect(page.locator('#copy')).toBeEnabled();
    }
  });
}
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
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
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
test('Jev setup validates, enables, tests, disables and removes without retaining the key',async({page})=>{
  const api=await standalone(page);
  await page.getByRole('button',{name:'Jev 设置'}).click();
  await expect(page.locator('#jev-state')).toHaveText('尚未配置，填入 Key 即可开始');
  await expect(page.locator('#jev-key')).toHaveAttribute('type','password');
  await expect(page.locator('#jev-enable')).toBeDisabled();
  await page.locator('#jev-key').fill('ui-test-secret');
  await page.getByRole('button',{name:'验证并启用'}).click();
  await expect(page.locator('#jev-state')).toHaveText('Jev 已启用');
  await expect(page.locator('#jev-key')).toHaveValue('');
  expect(await page.locator('body').innerText()).not.toContain('ui-test-secret');
  expect(await page.evaluate(()=>JSON.stringify([localStorage,sessionStorage]))).not.toContain('ui-test-secret');
  expect(api.calls.filter(c=>c.args.action==='jev_save')).toHaveLength(1);
  await page.getByRole('button',{name:'测试连接',exact:true}).click();
  await expect(page.locator('#jev-message')).toContainText('连接正常');
  await page.getByRole('button',{name:'停用',exact:true}).click();
  await expect(page.locator('#jev-state')).toHaveText('已配置 Key，Jev 未启用');
  await page.getByRole('button',{name:'移除本机 Key'}).click();
  await expect(page.locator('#jev-state')).toHaveText('尚未配置，填入 Key 即可开始');
});
test('Screenshot sending is a separate opt-in that starts off',async({page})=>{
  const api=await standalone(page);
  await page.getByRole('button',{name:'Jev 设置'}).click();
  const images=page.getByRole('checkbox',{name:'允许发送截图（视觉定位）'});
  await expect(images).not.toBeChecked();await expect(images).toBeEnabled();
  await images.click();
  await expect(page.locator('#jev-message')).toHaveText('已允许发送截图');
  await expect(images).toBeChecked();
  await expect(page.locator('#jev-state')).toHaveText('尚未配置，填入 Key 即可开始');
  await images.click();
  await expect(page.locator('#jev-message')).toHaveText('已关闭截图发送');
  expect(api.calls.filter(c=>c.args.action?.startsWith('jev_images')).map(c=>c.args.action)).toEqual(['jev_images_on','jev_images_off']);
});
test('Jev invalid-key feedback keeps the dialog usable and closing clears the input',async({page})=>{
  const api=await standalone(page);
  api.failSettings({ok:false,error:{code:'jev_http_error',status:401}});
  await page.getByRole('button',{name:'Jev 设置'}).click();
  await page.locator('#jev-key').fill('invalid-private-key');
  await page.getByRole('button',{name:'验证并启用'}).click();
  await expect(page.locator('#jev-message')).toHaveText('Key 无效或已失效');
  expect(api.calls.filter(c=>c.args.action==='jev_save')).toHaveLength(1);
  await page.getByRole('button',{name:'关闭设置'}).click();
  await expect(page.locator('#jev-key')).toHaveValue('');
  await page.getByRole('button',{name:'Jev 设置'}).click();
  await expect(page.locator('#jev-state')).toHaveText('尚未配置，填入 Key 即可开始');
});
test('Jev settings remain available during phone authentication pause',async({page})=>{
  const api=await standalone(page);
  api.set({...sample(),paused:true,frame:null,frame_available:false});
  await expect(page.locator('#app')).toHaveAttribute('data-state','paused');
  await page.getByRole('button',{name:'Jev 设置'}).click();
  await expect(page.locator('#jev-settings')).toBeVisible();
  await expect(page.locator('#jev-state')).toHaveText('尚未配置，填入 Key 即可开始');
  await expect(page.locator('#frame')).not.toHaveAttribute('src',/.+/);
  await expect(page.locator('#home')).toBeDisabled();
});
test('native MCP App handshakes and uses app-only tools without HTTP polling',async({page})=>{
  await page.route('http://native.test/**',route=>route.fulfill({contentType:'text/html',body:raw}));
  const host=`<iframe src="http://native.test/widget" style="width:380px;height:820px;border:0"></iframe>
    <script>window.calls=[];let jev=${JSON.stringify(jevSample())};window.addEventListener('message',e=>{const m=e.data;if(!m||m.id===undefined)return;
    window.calls.push(m);let result={};
    if(m.method==='ui/initialize')result={protocolVersion:m.params.protocolVersion,hostInfo:{name:'Test host',version:'1'},hostCapabilities:{serverTools:{}},hostContext:{displayMode:'fullscreen'}};
    if(m.method==='ui/request-display-mode')result={mode:'fullscreen'};
    if(m.method==='tools/call'){
      const action=m.params.arguments?.action;
      if(action==='jev_save')jev={...jev,enabled:true,configured:true,has_saved_key:true};
      result={content:[],structuredContent:action?.startsWith('jev_')?jev:${JSON.stringify(sample())}};
    }
    e.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*');});</script>`;
  await page.route('http://host.test/**',route=>route.fulfill({contentType:'text/html',body:host}));
  await page.goto('http://host.test/');
  const widget=page.frameLocator('iframe');
  await expect(widget.locator('#app')).toHaveAttribute('data-state','live');
  const names=await page.evaluate(()=>window.calls.filter(c=>c.method==='tools/call').map(c=>c.params.name));
  expect(names).toContain('aua_screen_frame');
  await widget.getByRole('button',{name:'Jev 设置'}).click();
  await widget.locator('#jev-key').fill('native-test-secret');
  await widget.getByRole('button',{name:'验证并启用'}).click();
  await expect(widget.locator('#jev-state')).toHaveText('Jev 已启用');
  await expect(widget.locator('#jev-key')).toHaveValue('');
  expect(await page.evaluate(()=>window.calls.some(c=>c.method==='tools/call'&&c.params.name==='aua_screen_action'&&c.params.arguments.action==='jev_save'))).toBeTruthy();
});
