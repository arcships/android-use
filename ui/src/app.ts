import { App } from '@modelcontextprotocol/ext-apps';

declare global { interface Window { __ANDROID_USE_STANDALONE__?: boolean } }
type Size = [number, number];
type Frame = {seq:number; data:string; mimeType:string; width:number; height:number};
type Preview = {ok:boolean; stream_id:string; seq:number; frame:Frame|null; frame_available:boolean;
  connected:boolean; capturing:boolean; paused:boolean; pause_reason:string|null;
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
const buttons=Object.fromEntries(['refresh','back','home','copy'].map(id=>[id,document.getElementById(id) as HTMLButtonElement]));
const standalone=window.__ANDROID_USE_STANDALONE__===true;
const app=standalone?undefined:new App({name:'Android Use Screen',version:'0.2.1'},
  {availableDisplayModes:['fullscreen']},{autoResize:false});
let preview:Preview|undefined,seq=0,stream='',disposed=false,inFlight=false,acting=false;
let suspended=false,timer:ReturnType<typeof setTimeout>|undefined;
let currentSource='',generation=0,initialized=false;
let noticeTimer:ReturnType<typeof setTimeout>|undefined;
const labels:Record<string,string>={connecting:'连接中',live:'Live',paused:'已暂停',offline:'未连接'};

function showNotice(message:string,detail=''){
  if(noticeTimer)clearTimeout(noticeTimer);
  notice.textContent=message;notice.title=detail;
  noticeTimer=setTimeout(()=>{notice.textContent='';notice.title='';},3000);
}

function setState(state:string,message?:string,detail?:string){
  root.dataset.state=state;statusText.textContent=labels[state];
  overlay.hidden=state==='live';
  if(!overlay.hidden){overlayText.textContent=message??'正在连接手机…';overlayDetail.textContent=detail??'实时视频将在这里显示';}
  for(const id of ['home','back','copy']) buttons[id].disabled=acting||state!=='live';
  buttons.refresh.disabled=acting;
}
async function call(endpoint:string,args:Record<string,unknown>={}):Promise<any>{
  if(standalone){
    const response=await fetch(`api/${endpoint}`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(args),signal:AbortSignal.timeout(endpoint==='action'?20000:8000)});
    if(!response.ok)throw new Error('preview_unreachable');
    return response.json();
  }
  const result=await app!.callServerTool({name:endpoint==='frame'?'aua_screen_frame':'aua_screen_action',
    arguments:endpoint==='frame'?args:endpoint==='refresh'?{action:'refresh'}:args},
    {timeout:endpoint==='action'?20000:8000});
  return result.structuredContent;
}
function apply(data:Preview){
  preview=data;document.querySelector('#model')!.textContent=data.device?.model||'Android';
  if(stream!==data.stream_id){stream=data.stream_id;seq=0;generation++;}
  if(data.paused){
    // Remove even the previously decoded frame from the DOM during authentication.
    generation++;frame.removeAttribute('src');currentSource='';
    setState('paused','请在手机上接管操作',data.pause_reason==='device_locked'?'手机已锁定，解锁后自动恢复':'认证完成后由 agent 恢复预览');return;
  }
  if(!data.connected){setState('offline','手机未连接','请检查 USB 或无线 ADB 连接');return;}
  if(data.error){setState('offline','视频连接暂不可用',data.error==='preview_dependency_missing'?'请安装 ffmpeg 后重连':'点击「重连」恢复视频');return;}
  const picture=data.frame;
  if(picture&&picture.seq>seq&&picture.data&&picture.width>0&&picture.height>0){
    seq=picture.seq;const expected=++generation;
    const source=`data:${picture.mimeType};base64,${picture.data}`;
    const image=new Image();image.onload=()=>{
      if(disposed||suspended||expected!==generation||preview?.paused)return;
      currentSource=source;frame.src=source;device.style.aspectRatio=`${picture.width}/${picture.height}`;
      root.style.setProperty('--screen-ratio',String(picture.width/picture.height));
      root.dataset.frameSeq=String(seq);setState('live');
    };image.onerror=()=>setState('offline','画面解码失败','点击「重连」重试');image.src=source;
  }else if(data.frame_available&&currentSource){setState('live');}
  else setState('connecting','正在连接视频…','通过 ADB 视频通道传输');
}
const visible=()=>!disposed&&!suspended&&document.visibilityState!=='hidden';
function schedule(delay=150){if(timer)clearTimeout(timer);if(visible())timer=setTimeout(poll,delay);}
async function poll(){
  if(!visible()||inFlight)return;
  inFlight=true;const started=generation;
  try{const data=await call('frame',{after_seq:seq});if(visible()&&started===generation)apply(data);}
  catch{if(visible())setState('offline','视频连接已断开','点击「重连」恢复');}
  finally{inFlight=false;schedule(preview?.paused?500:root.dataset.state==='offline'?1000:150);}
}
async function action(action:string,args:Record<string,unknown>={}){
  if(acting||preview?.paused)return;
  acting=true;notice.textContent='';for(const b of Object.values(buttons))b.disabled=true;
  try{const result=await call(action==='refresh'?'refresh':'action',{action,...args});
    if(!result?.ok)throw new Error(result?.error?.code||'action_failed');
    if(action==='refresh'){seq=0;apply(result);}
    showNotice(action==='refresh'?'已重新连接':'操作已执行');
  }catch(error){showNotice(String(error).includes('device_busy')?'手机正在操作，请稍后再试':'请查看手机当前状态');}
  finally{acting=false;setState(root.dataset.state||'connecting');schedule(50);}
}
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
  }catch{setState('offline','无法连接宿主','请重新打开 Android 手机屏幕');}
}
void start();
