/* Preview-only reading layout. Input is sanitized before this hook runs. */
(() => {
  'use strict';
  if(!window.WORKBENCH_PREVIEW_BASE)return;
  const body='color:#222222;font-family:PingFang SC,Microsoft YaHei,sans-serif;font-size:17px;line-height:1.95;background-color:#ffffff;text-align:left';
  const styles={h1:'font-size:32px;font-weight:800;line-height:1.5;margin:0 0 28px;color:#111111;border:0',
    h2:'font-size:30px;font-weight:800;line-height:1.5;margin:48px 0 24px;color:#111111;border:0',
    h3:'font-size:24px;font-weight:700;line-height:1.5;margin:40px 0 24px;color:#111111;border:0',
    p:'font-size:17px;line-height:1.95;margin:0 0 24px;color:#222222',
    blockquote:'margin:32px 0;padding:16px 20px;border-left:4px solid #FFCA00;background-color:#fffdf4;line-height:1.95;color:#333333',
    strong:'font-weight:700;color:#111111',b:'font-weight:700;color:#111111'};
  const number='font-size:32px;font-weight:800;line-height:1.5;color:#FFCA00';
  window.previewWechatSectionStyle=body;
  window.previewWechatStyle=root=>{
    for(const container of [root,...root.querySelectorAll('article,section,div')]){
      const nodes=[...container.children];
      const index=nodes.findIndex(node=>/^H[1-3]$/.test(node.tagName)&&/^(?:文章)?正文[：:]?$/.test(node.textContent.trim()));
      if(index>0&&nodes.slice(index+1).some(node=>node.tagName==='H1')&&/标题候选|标题建议|写作思路|文章结构说明/.test(nodes.slice(0,index).map(n=>n.textContent).join('\n')))
        nodes.slice(0,index+1).forEach(node=>node.remove());
    }
    const firstHeading=root.querySelector('h1,h2,h3');
    if(firstHeading&&/^(?:三个)?(?:标题候选|标题建议|写作思路|文章结构说明|营销分析|创作过程)[：:]?$/.test(firstHeading.textContent.trim()))
      throw Error('ARTICLE_BODY_UNCONFIRMED');
    root.querySelectorAll('article,section,div').forEach(node=>node.setAttribute('style',body));
    for(const [tag,style] of Object.entries(styles))root.querySelectorAll(tag).forEach(node=>node.setAttribute('style',style));
    root.querySelectorAll('h2').forEach((node,index)=>{
      const first=node.firstElementChild;
      if(first?.tagName==='SPAN'&&/^\d{2}$/.test(first.textContent)&&first.nextElementSibling?.tagName==='BR'&&first.style.color==='rgb(255, 202, 0)'){first.setAttribute('style',number);return;}
      const marker=document.createElement('span');marker.textContent=String(index+1).padStart(2,'0');marker.setAttribute('style',number);
      node.prepend(marker,document.createElement('br'));
    });
  };
})();
