"""Bounded server export sanitization; UI still uses its reviewed sanitizer."""
from html import escape
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET

from app.preview_article_references import clean_text,ArticleOutline

TAGS=set('article section div p span h1 h2 h3 h4 h5 h6 strong b em i u s blockquote ul ol li br hr table thead tbody tr th td pre code figure figcaption'.split())
DISCARD=set('script style iframe object embed svg math template noscript form head'.split())
VOID={'br','hr','img','input','link','meta'}
PROPERTIES=set('color background-color font-family font-size font-weight font-style text-align text-decoration line-height letter-spacing margin margin-top margin-bottom margin-left margin-right padding padding-top padding-bottom padding-left padding-right border border-left border-bottom border-top border-right border-radius width max-width height'.split())


def unwrap_article(content):
    # Match the already-supported Preview whole-document escaped wrapper only.
    # Recovered HTML is STILL parsed as untrusted data by ArticleTree below.
    if (isinstance(content,str) and re.match(r'\s*<section\s+id=["\']article["\']',content,re.I)
            and not re.search(r'<(?:h[1-6]|article|div|iframe|img)\b',content,re.I)):
        parser=ArticleOutline();parser.feed(content);parser.close()
        text=''.join(parser.parts).strip()
        text=re.sub(r'^```html\s*\n?|\n?```\s*$','',text,flags=re.I).strip()
        if re.match(r'^<(?:div|section|article)\b',text,re.I) and re.search(r'</(?:div|section|article)>\s*$',text,re.I):return text
    return content


class ArticleTree(HTMLParser):
    def __init__(self,content,preserve_images=False):
        super().__init__(convert_charrefs=True)
        if not isinstance(content,str) or not 0<len(content)<=256*1024:raise ValueError('文章正文无效或过大。')
        content=unwrap_article(content)
        self.root=ET.Element('article');self.stack=[self.root];self.skip=[];self.count=0;self.preserve_images=preserve_images
        match=re.fullmatch(r'\s*```html\s*\n([\s\S]*?)\n```\s*',content,re.I)
        self.feed(match[1] if match else content);self.close()
        if not ''.join(self.root.itertext()).strip():raise ValueError('文章没有可用正文。')
        from app.preview_wechat_article import normalize_tree
        normalize_tree(self.root)

    def handle_starttag(self,tag,attrs):
        self.count+=1
        if self.count>10000 or len(self.stack)>64:raise ValueError('文章结构过于复杂。')
        if self.skip:
            if tag in DISCARD:self.skip.append(tag)
            return
        if tag in DISCARD:self.skip.append(tag);return
        if tag=='img':
            # Normal illustration exports still use the persisted asset map.
            # The Preview article renderer may retain same-site guarded images;
            # actual reading remains subject to their normal authorization.
            src=dict(attrs).get('src','')
            if self.preserve_images and re.fullmatch(r'/(?:preview/)?api/v1/(?:storage/(?:generated|assets)/[A-Za-z0-9_./-]+|chat-images/[A-Za-z0-9_-]+)',src) and '..' not in src:
                ET.SubElement(self.stack[-1],'img',{'src':src,'alt':clean_text(dict(attrs).get('alt','文章图片'))[:200],'style':'width:100%;height:auto'})
            return
        if tag not in TAGS:return
        element=ET.SubElement(self.stack[-1],tag)
        styles=[]
        for item in dict(attrs).get('style','').split(';'):
            name,separator,value=item.partition(':');name=name.strip().lower();value=value.strip()
            if (separator and name in PROPERTIES and len(value)<180
                    and re.fullmatch(r"[#a-zA-Z0-9.,%\s+'\"-]+",value)
                    and not re.search(r'url|expression|javascript|behavior|position',value,re.I)
                    and not any(float(n)<0 or float(n)>64 for n in re.findall(r'(-?\d+(?:\.\d+)?)px',value))):
                styles.append(name+':'+value)
        if styles:element.set('style',';'.join(styles))
        if tag not in VOID:self.stack.append(element)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if self.skip:
            if tag==self.skip[-1]:self.skip.pop()
            return
        for index in range(len(self.stack)-1,0,-1):
            if self.stack[index].tag==tag:self.stack=self.stack[:index];break

    def handle_data(self,data):
        if self.skip:return
        parent=self.stack[-1];data=clean_text(data)
        if len(parent):parent[-1].tail=(parent[-1].tail or '')+data
        else:parent.text=(parent.text or '')+data

    def positions(self):
        return [node for node in self.root.iter() if node.tag in {'h1','h2','h3','p'} and ''.join(node.itertext()).strip()]

    def html(self,assets=(),portable=False):
        positions=self.positions();parents={child:parent for parent in self.root.iter() for child in parent}
        for asset in assets:
            figure=ET.Element('figure',{'style':'margin:24px 0'})
            src='images/'+asset['filename'] if portable else asset['url']
            ET.SubElement(figure,'img',{'src':src,'alt':asset['label'],'style':'width:100%;height:auto'})
            ET.SubElement(figure,'figcaption',{'style':'font-size:12px;color:#666666;text-align:center'}).text='AI配图示意，非真实活动照片 · '+asset['label']
            index=asset['position']
            if positions and 0<=index<len(positions):
                target=positions[index];parent=parents.get(target,self.root);parent.insert(list(parent).index(target)+1,figure)
            else:self.root.append(figure)
        return ET.tostring(self.root,encoding='unicode',method='html')


def full_document(body,title,portable=False):
    # UI preview has its own opaque sandbox; this is a safe downloadable file.
    policy="default-src 'none'; style-src 'unsafe-inline'; img-src 'self'"+(' file:' if portable else '')+"; base-uri 'none'; form-action 'none'"
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+policy+'"><title>'+escape(title)+'</title><style>body{max-width:680px;margin:0 auto;padding:24px;font:17px/1.95 sans-serif;color:#222;background:#fff}img{max-width:100%;height:auto}table{max-width:100%;overflow-wrap:anywhere}*{box-sizing:border-box;overflow-wrap:anywhere}@media(max-width:480px){body{padding:20px 16px}h1,h2{font-size:28px!important}p{font-size:16px!important}}</style></head><body>'+body+'</body></html>'
