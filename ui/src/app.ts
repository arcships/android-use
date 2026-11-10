import { App } from '@modelcontextprotocol/ext-apps';

declare global { interface Window { __ANDROID_USE_STANDALONE__?: boolean } }
type Size = [number, number];
type Frame = {seq:number; data:string; mimeType:string; width:number; height:number};
type Preview = {ok:boolean; stream_id:string; seq:number; frame:Frame|null; frame_available:boolean;
  connected:boolean; capturing:boolean; paused:boolean; pause_reason:string|null; interaction_ready?:boolean;
  device:{model:string}; viewport:Size|null; error:string|null};
const root=document.querySelector<HTMLElement>('#app')!;
const frame=document.querySelector<HTMLImageElement>('#frame')!;
const device=document.querySelector<HTMLElement>('#device')!;
const overlay=document.querySelector<HTMLElement>('#overlay')!;
const overlayText=document.querySelector<HTMLElement>('#overlay-text')!;
const overlayDetail=document.querySelector<HTMLElement>('#overlay-detail')!;
const statusText=document.querySelector<HTMLElement>('#status-text')!;
const notice=document.querySelector<HTMLElement>('#notice')!;
const pointer=document.querySelector<HTMLElement>('#pointer')!;
const resumeButton=document.querySelector<HTMLButtonElement>('#resume')!;
const buttons=Object.fromEntries(['refresh','back','home','copy'].map(id=>[id,document.getElementById(id) as HTMLButtonElement]));
const standalone=window.__ANDROID_USE_STANDALONE__===true;
const app=standalone?undefined:new App({name:'Android Use Screen',version:'0.7.1'},
  {availableDisplayModes:['fullscreen']},{autoResize:false});
let preview:Preview|undefined,seq=0,stream='',disposed=false,inFlight=false,acting=false;
let suspended=false,timer:ReturnType<typeof setTimeout>|undefined;
let currentSource='',generation=0,initialized=false;
let reconnectRequested=false;
const recoveryGraceMs=5000;
let interruptedAt:number|undefined,waitingAfterSeq:number|undefined;
let noticeTimer:ReturnType<typeof setTimeout>|undefined;
type JevStatus={ok:boolean;enabled:boolean;configured:boolean;has_saved_key:boolean;images_enabled?:boolean;key_source:string;model:string;base_url:string;tested?:boolean;latency_ms?:number;demo?:boolean;error?:{code:string;status?:number}};
const settingsButton=document.querySelector<HTMLButtonElement>('#settings')!;
const settingsDialog=document.querySelector<HTMLDialogElement>('#jev-settings')!;
const keyInput=document.querySelector<HTMLInputElement>('#jev-key')!;
const settingsMessage=document.querySelector<HTMLElement>('#jev-message')!;
const enableJev=document.querySelector<HTMLButtonElement>('#jev-enable')!;
const testJev=document.querySelector<HTMLButtonElement>('#jev-test')!;
const disableJev=document.querySelector<HTMLButtonElement>('#jev-disable')!;
const clearJev=document.querySelector<HTMLButtonElement>('#jev-clear')!;
const imagesJev=document.querySelector<HTMLInputElement>('#jev-images')!;
let jevStatus:JevStatus|undefined,settingsBusy=false;
const labels:Record<string,string>={connecting:'连接中',live:'Live',buffering:'缓冲中',paused:'已暂停',offline:'未连接',unsupported:'不支持预览'};

function showNotice(message:string,detail=''){
  if(noticeTimer)clearTimeout(noticeTimer);
  notice.textContent=message;notice.title=detail;
  noticeTimer=setTimeout(()=>{notice.textContent='';notice.title='';},3000);
}

function setState(state:string,message?:string,detail?:string){
  root.dataset.state=state;statusText.textContent=labels[state];
  overlay.hidden=state==='live'||state==='buffering';
  if(!overlay.hidden){overlayText.textContent=message??'正在连接手机…';overlayDetail.textContent=detail??'实时视频将在这里显示';}
  // Only an authentication takeover can be ended here; a locked phone resumes by itself.
  resumeButton.hidden=!(state==='paused'&&preview?.pause_reason==='authentication');
  for(const id of ['home','back','copy']) buttons[id].disabled=acting||state!=='live';
  buttons.refresh.disabled=acting;
}
async function call(endpoint:string,args:Record<string,unknown>={}):Promise<any>{
  if(standalone){
    const response=await fetch(`api/${endpoint}`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(args),signal:AbortSignal.timeout(endpoint==='action'?20000:8000)});
    if(!response.ok){
      if(endpoint==='action'&&String(args.action).startsWith('jev_')){
        try{const data=await response.json();if(data?.error?.code)return data;}catch{}
      }
      throw new Error('preview_unreachable');
    }
    return response.json();
  }
  const result=await app!.callServerTool({name:endpoint==='frame'?'aua_screen_frame':'aua_screen_action',
    arguments:endpoint==='frame'?args:endpoint==='refresh'?{action:'refresh'}:args},
    {timeout:endpoint==='action'?20000:8000});
  return result.structuredContent;
}
function renderJev(data:JevStatus){
  jevStatus=data;settingsButton.dataset.enabled=String(data.enabled);
  document.querySelector('#jev-state')!.textContent=data.demo?'文档示例，请在实际手机屏幕中配置':data.enabled?'Jev 已启用':data.configured?'已配置 Key，Jev 未启用':'尚未配置，填入 Key 即可开始';
  document.querySelector('#jev-provider')!.textContent=`${data.model} · ${data.base_url}`;
  keyInput.placeholder=data.configured?'已有 Key，留空继续使用':'粘贴你的 API Key';
  keyInput.disabled=!!data.demo||settingsBusy;enableJev.disabled=!!data.demo||settingsBusy||(!data.configured&&!keyInput.value.trim());testJev.disabled=!data.configured||!!data.demo||settingsBusy;
  disableJev.hidden=!data.enabled;clearJev.hidden=!data.has_saved_key;disableJev.disabled=settingsBusy;clearJev.disabled=settingsBusy;
  imagesJev.checked=!!data.images_enabled;imagesJev.disabled=!!data.demo||settingsBusy;
}
function jevError(data?:JevStatus):string{
  if(data?.error?.code==='jev_http_error')return data.error.status===401?'Key 无效或已失效':data.error.status===429?'请求过于频繁，请稍后重试':'服务拒绝请求，请检查 Key 权限和账户状态';
  const errors:Record<string,string>={jev_configuration:'请填写有效的 TypeSafe API Key',jev_transport_error:'无法连接 TypeSafe，请检查网络后重试',jev_settings_unavailable:'无法读取或保存本机设置，请检查文件权限',jev_validation_failed:'连接测试未通过，设置尚未更改',device_busy:'另一项设置操作正在进行，请稍后重试'};
  return errors[data?.error?.code||'']||'设置操作未完成，请重新打开设置后重试';
}
async function configureJev(action:string){
  if(settingsBusy||!jevStatus||jevStatus.demo)return;
  settingsBusy=true;settingsMessage.textContent=action==='jev_save'?'正在验证 Key…':action==='jev_test'?'正在测试连接…':'正在更新设置…';settingsMessage.dataset.error='false';
  keyInput.disabled=true;settingsButton.disabled=true;document.querySelector<HTMLButtonElement>('#settings-close')!.disabled=true;for(const b of [enableJev,testJev,disableJev,clearJev,imagesJev])b.disabled=true;
  try{
    const data:JevStatus=await call('action',{action,...(action==='jev_save'?{api_key:keyInput.value}:{})});
    if(!data?.ok){settingsMessage.textContent=jevError(data);settingsMessage.dataset.error='true';return;}
    if(!action.startsWith('jev_images'))keyInput.value='';renderJev(data);
    settingsMessage.textContent=data.tested?`${action==='jev_save'?'已保存并启用':'连接正常'} · ${data.latency_ms} ms`:action==='jev_images_on'?'已允许发送截图':action==='jev_images_off'?'已关闭截图发送':action==='jev_clear'?'本机 Key 已移除，Jev 已停用':'Jev 已停用';
  }catch{settingsMessage.textContent='无法连接本机设置服务，请重新打开手机屏幕';settingsMessage.dataset.error='true';}
  finally{settingsBusy=false;settingsButton.disabled=false;document.querySelector<HTMLButtonElement>('#settings-close')!.disabled=false;if(jevStatus)renderJev(jevStatus);}
}
settingsButton.onclick=()=>{
  if(settingsBusy)return;
  jevStatus=undefined;keyInput.value='';keyInput.disabled=true;settingsMessage.textContent='';settingsDialog.showModal();
  enableJev.disabled=true;testJev.disabled=true;disableJev.hidden=true;clearJev.hidden=true;imagesJev.disabled=true;imagesJev.checked=false;
  void call('action',{action:'jev_status'}).then((data:JevStatus)=>{
    if(!data?.ok)throw new Error('settings_unavailable');renderJev(data);
  }).catch(()=>{document.querySelector('#jev-state')!.textContent='无法读取设置，请重新打开手机屏幕';});
};
document.querySelector<HTMLFormElement>('#jev-form')!.onsubmit=event=>{event.preventDefault();void configureJev('jev_save');};
imagesJev.onchange=()=>{const on=imagesJev.checked;if(jevStatus)imagesJev.checked=!!jevStatus.images_enabled;void configureJev(on?'jev_images_on':'jev_images_off');};
testJev.onclick=()=>void configureJev('jev_test');disableJev.onclick=()=>void configureJev('jev_disable');clearJev.onclick=()=>void configureJev('jev_clear');
document.querySelector<HTMLButtonElement>('#settings-close')!.onclick=()=>settingsDialog.close();
settingsDialog.addEventListener('close',()=>{keyInput.value='';});
settingsDialog.addEventListener('cancel',event=>{if(settingsBusy)event.preventDefault();});
keyInput.oninput=()=>{if(jevStatus)renderJev(jevStatus);};
function clearFrame(){
  generation++;frame.removeAttribute('src');currentSource='';seq=0;
}
function recovering(message:string,keepFrame=false,serviceUnavailable=false){
  interruptedAt??=performance.now();
  const expired=performance.now()-interruptedAt>=recoveryGraceMs;
  if(!expired&&keepFrame&&currentSource){setState('buffering');return;}
  if(expired)clearFrame();
  setState(expired?'offline':'connecting',expired?(serviceUnavailable?'本地预览服务未响应':'视频暂不可用'):message,
    expired?(serviceUnavailable?'正在自动重试；若持续失败，请让 Agent 重新打开手机屏幕':'正在自动重试，也可点击「重连」'):'正在自动恢复，无需手动重连');
}
function showPictureState(){
  const state=preview?.interaction_ready===false?'buffering':'live';
  setState(state);
  if(state==='live'&&reconnectRequested){reconnectRequested=false;showNotice('已重新连接');}
}
function apply(data:Preview){
  if(!data?.ok)throw new Error('preview_unreachable');
  preview=data;document.querySelector('#model')!.textContent=data.device?.model||'Android';
  if(stream!==data.stream_id){stream=data.stream_id;clearFrame();waitingAfterSeq=undefined;}
  if(data.paused){
    // Remove even the previously decoded frame from the DOM during authentication.
    clearFrame();waitingAfterSeq=undefined;interruptedAt=undefined;
    setState('paused','请在手机上接管操作',data.pause_reason==='device_locked'?'手机已锁定，解锁后自动恢复':'认证完成后点击下方按钮，或由 agent 恢复预览');return;
  }
  if(!data.connected){clearFrame();setState('offline','手机未连接','请检查 USB 或无线 ADB 连接');return;}
  if(data.error==='video_stream_ended'){
    // Unexpected encoder exits recover in the daemon; normal segment boundaries
    // stay on one pipe. Require a new picture before re-enabling phone controls.
    if(waitingAfterSeq===undefined){waitingAfterSeq=data.seq;generation++;}
    recovering('正在恢复视频…',true);return;
  }
  if(data.error==='preview_unsupported_device'){
    // Permanent device capability, not a transient failure: don't promise a retry.
    clearFrame();setState('unsupported','此手机不支持实时预览','系统未提供 screenrecord；截图、读屏和操作仍可正常使用');return;
  }
  if(data.error){clearFrame();setState('offline','视频连接暂不可用',data.error==='preview_dependency_missing'?'请安装 ffmpeg 后重连':'正在自动重试，也可点击「重连」');return;}
  if(waitingAfterSeq!==undefined&&data.seq<=waitingAfterSeq){recovering('正在等待新画面…',true);return;}
  const picture=data.frame;
  if(picture&&picture.seq>seq&&picture.data&&picture.width>0&&picture.height>0){
    seq=picture.seq;const expected=++generation;
    const source=`data:${picture.mimeType};base64,${picture.data}`;
    const image=new Image();image.onload=()=>{
      if(disposed||suspended||expected!==generation||preview?.paused)return;
      currentSource=source;frame.src=source;device.style.aspectRatio=`${picture.width}/${picture.height}`;
      root.style.setProperty('--screen-ratio',String(picture.width/picture.height));
      root.dataset.orientation=picture.width>picture.height?'landscape':'portrait';
      root.dataset.frameSeq=String(seq);interruptedAt=undefined;waitingAfterSeq=undefined;
      showPictureState();
    };image.onerror=()=>setState('offline','画面解码失败','点击「重连」重试');image.src=source;
  }else if(data.frame_available&&currentSource){interruptedAt=undefined;showPictureState();}
  else if(currentSource){
    // Rotation, a refreshed encoder or returning to a hidden viewer may need a
    // first frame. Keep the last picture visible, with interactions disabled.
    if(waitingAfterSeq===undefined){waitingAfterSeq=data.seq;generation++;}
    recovering('正在等待新画面…',true);
  }
  else if(interruptedAt!==undefined)recovering('正在等待新画面…');
  else setState('connecting','正在连接视频…','通过 ADB 视频通道传输');
}
const visible=()=>!disposed&&!suspended&&document.visibilityState!=='hidden';
function schedule(delay=150){if(timer)clearTimeout(timer);if(visible())timer=setTimeout(poll,delay);}
async function poll(){
  if(!visible()||inFlight)return;
  inFlight=true;const started=generation;
  try{const data=await call('frame',{after_seq:seq});if(visible()&&started===generation)apply(data);}
  catch{if(visible()&&started===generation){clearFrame();recovering('视频连接恢复中…',false,true);}}
  finally{inFlight=false;schedule(preview?.paused?500:root.dataset.state==='offline'?1000:150);}
}
async function action(action:string,args:Record<string,unknown>={}){
  if(acting||preview?.paused)return;
  acting=true;notice.textContent='';for(const b of Object.values(buttons))b.disabled=true;
  if(action==='refresh'){
    reconnectRequested=true;waitingAfterSeq=seq;generation++;
    showNotice('正在重连…');
  }
  try{const result=await call(action==='refresh'?'refresh':'action',{action,...args});
    if(!result?.ok)throw new Error(result?.error?.code||'action_failed');
    if(action==='refresh')apply(result);
  }catch(error){
    if(action==='refresh'){
      reconnectRequested=false;
      showNotice(String(error).includes('device_busy')?'手机正在操作，请稍后再试':'重连尚未完成，请让 Agent 重新打开手机屏幕');
    }
  }
  finally{acting=false;setState(root.dataset.state||'connecting');schedule(50);}
}
// The user's own click is the takeover confirmation; it clears the same flag as aua_resume.
resumeButton.onclick=async()=>{
  if(resumeButton.disabled)return;
  resumeButton.disabled=true;
  try{const result=await call('action',{action:'resume'});if(!result?.ok)throw new Error('resume_failed');}
  catch{showNotice('恢复未完成，请重试或让 Agent 恢复');}
  finally{resumeButton.disabled=false;schedule(0);}
};
buttons.refresh.onclick=()=>{if(preview?.paused){showNotice('接管期间保持暂停');return;}void action('refresh');};
buttons.home.onclick=()=>void action('home');buttons.back.onclick=()=>void action('back');
buttons.copy.onclick=()=>{
  if(acting||preview?.paused||root.dataset.state!=='live'||!frame.naturalWidth)return;
  if(!navigator.clipboard?.write||typeof ClipboardItem==='undefined'){
    showNotice('当前环境不支持复制图片');return;
  }
  // Capture the displayed frame synchronously. A promise preserves the original
  // clipboard user gesture while PNG encoding finishes, including on WebKit.
  const canvas=document.createElement('canvas');canvas.width=frame.naturalWidth;canvas.height=frame.naturalHeight;
  const context=canvas.getContext('2d');if(!context)return;
  context.drawImage(frame,0,0);
  const png=new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('encode_failed')),'image/png'));
  acting=true;setState(root.dataset.state||'live');
  void navigator.clipboard.write([new ClipboardItem({'image/png':png})])
    .then(()=>showNotice('画面已复制'))
    .catch(()=>showNotice('复制失败，请允许写入剪贴板'))
    .finally(()=>{acting=false;setState(root.dataset.state||'live');});
};
let down:{x:number;y:number;at:number;stream:string;size:Size}|undefined;
function point(event:PointerEvent):[number,number]|undefined{
  if(root.dataset.state!=='live'||acting||!preview?.viewport)return;
  const box=frame.getBoundingClientRect(),[w,h]=preview.viewport;
  if(Math.abs(box.width/box.height-w/h)>.08)return; // Don't target letterboxed/rotating frames.
  return [Math.max(0,Math.min(w-1,Math.round((event.clientX-box.left)/box.width*w))),
    Math.max(0,Math.min(h-1,Math.round((event.clientY-box.top)/box.height*h)))];
}
device.onpointerdown=event=>{const p=point(event);if(!p||!preview?.viewport)return;
  down={x:p[0],y:p[1],at:performance.now(),stream,size:[...preview.viewport]};
  device.setPointerCapture(event.pointerId);pointer.style.display='block';
  pointer.style.left=`${p[0]/down.size[0]*100}%`;pointer.style.top=`${p[1]/down.size[1]*100}%`;
};
device.onpointerup=event=>{pointer.style.display='none';const start=down;down=undefined;const p=point(event);
  if(!start||!p||start.stream!==stream||start.size.join()!==preview?.viewport?.join())return;
  const dx=p[0]-start.x,dy=p[1]-start.y;
  if(Math.hypot(dx,dy)<20)void action('tap',{x:p[0],y:p[1]});
  else void action('swipe',{direction:Math.abs(dx)>Math.abs(dy)?(dx>0?'right':'left'):(dy>0?'down':'up')});
};
device.onpointercancel=()=>{down=undefined;pointer.style.display='none';};
document.addEventListener('visibilitychange',()=>{if(document.hidden){generation++;if(timer)clearTimeout(timer);}else schedule(0);});
window.addEventListener('pagehide',()=>{disposed=true;generation++;if(timer)clearTimeout(timer);if(noticeTimer)clearTimeout(noticeTimer);});
if(app){
  app.ontoolresult=()=>{if(initialized)schedule(0);};
  app.onhostcontextchanged=context=>{
    const next=context.displayMode==='inline'; // Fullscreen is the live sidebar; hidden/inline doesn't capture.
    suspended=next;if(next){generation++;if(timer)clearTimeout(timer);}else schedule(0);
  };
}
async function start(){
  setState('connecting');
  try{if(app){await app.connect(undefined,{timeout:8000});
    try{await app.requestDisplayMode({mode:'fullscreen'},{timeout:3000});}catch{/* Host may already be fullscreen. */}}
    initialized=true;schedule(0);
    void call('action',{action:'jev_status'}).then((data:JevStatus)=>{if(data?.ok)renderJev(data);}).catch(()=>{});
  }catch{setState('offline','无法连接宿主','请重新打开 Android 手机屏幕');}
}
void start();
