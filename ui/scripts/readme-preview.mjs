// Local documentation preview: real widget UI, synthetic device pixels, no ADB.
// Run from ui/: node scripts/readme-preview.mjs
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';

const root=new URL('../../',import.meta.url);
const html=(await readFile(new URL('src/android_use/assets/phone-screen.html',root),'utf8'))
  .replace('<!--__MODE__-->','<script>window.__ANDROID_USE_STANDALONE__=true</script>');
const pixels=(await readFile(new URL('docs/images/demo-screen.svg',root))).toString('base64');
const sample={ok:true,stream_id:'readme-demo',seq:1,
  frame:{seq:1,data:pixels,mimeType:'image/svg+xml',width:540,height:1200},
  frame_available:true,paused:false,pause_reason:null,connected:true,capturing:true,
  device:{model:'Android · Demo'},viewport:[1080,2400],error:null};
const server=createServer((req,res)=>{
  res.setHeader('Cache-Control','no-store');
  if(req.method==='GET'&&req.url==='/'){
    res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(html);
  }
  if(req.method==='POST'&&req.url==='/api/frame'){
    res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(sample));
  }
  res.writeHead(404);res.end();
});
server.listen(0,'127.0.0.1',()=>console.log('http://127.0.0.1:'+server.address().port+'/'));
process.on('SIGTERM',()=>server.close());
