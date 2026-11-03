import { build } from 'esbuild';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';
const result = await build({entryPoints:['src/app.ts'],bundle:true,format:'esm',target:'es2022',
  minify:true,write:false,metafile:true,pure:['console.debug'],legalComments:'inline'});
const html = await readFile('index.html','utf8');
const css = await readFile('style.css','utf8');
await writeFile('../src/android_use/assets/phone-screen.html',html
  .replace('/*__STYLE__*/',()=>css)
  .replace('/*__SCRIPT__*/',()=>result.outputFiles[0].text.replace(/<\/script/gi,'<\\/script')));

// Preserve license texts for precisely the packages included in the browser bundle.
const packages=new Map();
for(const input of Object.keys(result.metafile.inputs)){
  if(!input.includes(`node_modules${sep}`))continue;
  let directory=dirname(resolve(input));
  while(directory!==dirname(directory)){
    try{
      const pkg=JSON.parse(await readFile(join(directory,'package.json'),'utf8'));
      if(pkg.name&&pkg.version){packages.set(`${pkg.name}@${pkg.version}`,{directory,pkg});break;}
    }catch{/* Continue to the containing package root. */}
    directory=dirname(directory);
  }
}
const notices=['# Third-party notices','',
  'Android Use source code is licensed under MIT. The bundled browser SDK and its dependencies retain their own licenses, reproduced below. Python dependencies and ffmpeg are installed separately and retain their upstream licenses.',''];
for(const [name,{directory,pkg}] of [...packages.entries()].sort()){
  notices.push(`## ${name}`,'',`Declared license: ${typeof pkg.license==='string'?pkg.license:JSON.stringify(pkg.license)}`,'');
  const licenseFiles=(await readdir(directory)).filter(name=>/^(licen[sc]e|notice|copying)([.\-_]|$)/i.test(name)).sort();
  for(const name of licenseFiles){
    try{notices.push(`### ${name}`,'','```text',(await readFile(join(directory,name),'utf8')).trimEnd(),'```','');}
    catch{/* Some packages use a directory for multi-license notices. */}
  }
  if(!licenseFiles.length)notices.push('See the package source and retained notices in the browser bundle.','');
}
await writeFile('../THIRD_PARTY_NOTICES.md',notices.join('\n'));
