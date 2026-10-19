"""Preview-only body-first writing and reading styles; immutable Skill unchanged."""
from dataclasses import replace
import os,re
import xml.etree.ElementTree as ET
from app.wechat_article_format import render_wechat_article as _base_render

BODY_POLICY='''Preview公众号交付要求（优先于旧Skill中三个标题候选等交付包装要求）：
直接输出一篇完整文章，只保留一个正式文章标题、导语、章节标题、正文和结尾。
不要三个标题候选、写作思路、结构说明、创作过程、无关前言、营销分析或结尾AI说明。
使用一个Markdown一级标题与自然的二级章节，正文段落清晰。不要输出第二份HTML或CSS；平台会安全排版。
标题建议、摘要、封面建议不是正文；用户没有要求时不主动生成这些字段。保留真实正文事实，不编造案例或效果。
只有真实业务事实需要核验时调用相应企业工具；素材足够时直接完成。文章排版不代表创建了微信草稿。
'''
BODY='color:#222222;font-family:PingFang SC,Microsoft YaHei,sans-serif;font-size:17px;line-height:1.95;background-color:#ffffff;text-align:left'
STYLES={
 'h1':'font-size:32px;font-weight:800;line-height:1.5;margin:0 0 28px;color:#111111;border:0',
 'h2':'font-size:30px;font-weight:800;line-height:1.5;margin:48px 0 24px;color:#111111;border:0',
 'h3':'font-size:24px;font-weight:700;line-height:1.5;margin:40px 0 24px;color:#111111;border:0',
 'p':'font-size:17px;line-height:1.95;margin:0 0 24px;color:#222222',
 'blockquote':'margin:32px 0;padding:16px 20px;border-left:4px solid #FFCA00;background-color:#fffdf4;line-height:1.95;color:#333333',
 'strong':'font-weight:700;color:#111111','b':'font-weight:700;color:#111111',
}
NUMBER='font-size:32px;font-weight:800;line-height:1.5;color:#FFCA00'


def body_only_markdown(text):
    lines=text.splitlines()
    markers=[i for i,line in enumerate(lines) if re.fullmatch(r'#{1,3}\s*(?:文章)?正文[：:]?\s*',line)]
    for i in markers:
        # Explicit packaging marker + an actual title. No substring-based
        # paragraph deletion; ambiguous historical content remains unchanged.
        if re.search(r'标题候选|标题建议|写作思路|文章结构说明','\n'.join(lines[:i])) and any(re.match(r'^#\s+\S',line) for line in lines[i+1:]):
            return '\n'.join(lines[i+1:]).strip()
    return text


def normalize_tree(root):
    root.set('style',BODY)
    count=0
    for node in root.iter():
        if node.tag in {'section','article','div'}:node.set('style',BODY)
        if node.tag in STYLES:node.set('style',STYLES[node.tag])
        if node.tag=='h2':
            count+=1;children=list(node)
            if (len(children)>=2 and children[0].tag=='span' and re.fullmatch(r'\d{2}',children[0].text or '')
                    and '#ffca00' in children[0].get('style','').lower() and children[1].tag=='br'):
                children[0].set('style',NUMBER);continue
            text=node.text;node.text=None
            number=ET.Element('span',{'style':NUMBER});number.text=f'{count:02d}'
            br=ET.Element('br');br.tail=text
            node.insert(0,number);node.insert(1,br)
    return root


def render(text):
    from app.preview_illustration_html import ArticleTree
    html=_base_render(body_only_markdown(text))
    tree=ArticleTree(html,preserve_images=True)
    return tree.html()


def install():
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true':raise RuntimeError('Preview only')
    from app.agent_catalog import CATALOG
    agent=CATALOG['preview-wechat-agent']
    if BODY_POLICY not in agent.instructions:CATALOG[agent.id]=replace(agent,instructions=agent.instructions+'\n'+BODY_POLICY)
    from app import wechat_article_format
    wechat_article_format.render_wechat_article=render


def provider_request(body,task):
    if task and task['agent_id']=='preview-wechat-agent':
        text=body.get('instructions') or ''
        if BODY_POLICY not in text:return {**body,'instructions':text+'\n'+BODY_POLICY}
    return body
