import { build } from 'esbuild';
import { cp, mkdir } from 'node:fs/promises';
await mkdir('workshop/web/assets', {recursive: true});
await build({entryPoints:['frontend/bootstrap.js'],outfile:'workshop/web/assets/app.js',bundle:true,minify:true,format:'esm',target:'es2022',sourcemap:true});
await cp('frontend/index.html','workshop/web/index.html');
await cp('frontend/style.css','workshop/web/assets/style.css');
