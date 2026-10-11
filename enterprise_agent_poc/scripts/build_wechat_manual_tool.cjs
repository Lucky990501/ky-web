// Reproducible offline artifact, no network or new preview framework.
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..'),dir=path.join(root,'tools/wechat-manual-export');
const ui=fs.readFileSync(path.join(root,'app/static/wechat-article-ui.js'),'utf8');
const workbench=fs.readFileSync(path.join(root,'app/static/workbench.js'),'utf8');
const css=fs.readFileSync(path.join(root,'app/static/workbench.css'),'utf8');
const slice=(text,start,end)=>{const first=text.indexOf(start),last=text.indexOf(end,first);if(first<0||last<=first)throw Error('SHARED_SOURCE_BOUNDARY_CHANGED');return text.slice(first,last);};
const helpers=slice(workbench,'const escapeHtml =','const icon =')
  +slice(workbench,'const markdownInlineHtml =','const activityPlanResultIsValid =')
  +slice(workbench,'function bindModalDialog(','function showHelpDialog(');
const sharedCSS=slice(css,'.wechat-article-actions{','@media(max-width:600px)')
  +css.match(/@media\(max-width:600px\)\{\.wechat-preview-dialog[^\n]+/)[0];
let output=fs.readFileSync(path.join(dir,'template.html'),'utf8');
for(const [marker,value]of [['/* SHARED_ARTICLE_CSS */',sharedCSS],['/* SHARED_ARTICLE_JS */',ui],['/* SHARED_WORKBENCH_HELPERS */',helpers]])output=output.replace(marker,()=>value.replace(/<\/script/gi,'<\\/script'));
const target=path.join(dir,'standalone.html');
if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('OFFLINE_ARTIFACT_STALE');console.log('OFFLINE_ARTIFACT_MATCH');}
else{fs.writeFileSync(target,output);console.log('Generated tools/wechat-manual-export/standalone.html');}
