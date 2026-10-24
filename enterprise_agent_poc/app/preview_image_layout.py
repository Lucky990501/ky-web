"""Small Preview-only brochure semantics and original-Logo brand zones."""
import io
import json
import re
from pathlib import PurePosixPath
from PIL import Image,ImageStat
from app.brand_logo import inspect_transparent_png

BROCHURE_SIZE='1536x1024'  # Existing verified gateway enum, NOT a new size.
BROCHURE_RATIO='3:2'       # Nearest existing landscape enum to A4's 297:210.


def side(text):
    front=bool(re.search(r'正面|正页|封面|一正',text))
    back=bool(re.search(r'反面|背面|背页|一反',text))
    return 'front' if front and not back else 'back' if back and not front else None


def brochure(text,base=''):
    if not re.search(r'三折页|折页展开|折页正反',text) and re.search(r'(?:改成|换成|做成|转换为|改为|生成|设计|制作).{0,8}(?:海报|封面|长图)',text):return False
    return bool(re.search(r'三折页|折页展开|折页正反',text+'\n'+base))


def explicit_geometry(text):
    return bool(re.search(r'\d{2,5}\s*[xX×*]\s*\d{2,5}|\d{1,2}\s*[:：]\s*\d{1,2}|横版|竖版|方[形图]',text))


def layout_prompt(mode):
    if mode not in {'brochure_front','brochure_back'}:return ''
    outer=mode=='brochure_front'
    return ('\n设计类型是成熟的三折页'+('外侧正面' if outer else '内侧反面')+'平面展开稿，不是单页海报、折叠透视样机或正反面拼图。'
        '横向画布三个折页面板，等宽逻辑、折缝两侧留出安全距离，文字与人脸不可跨折缝；不拉伸人物、文字或Logo。'
        +('外侧从左到右：背页联系/行动区、折入页简洁引导、右侧封面主标题/品牌/主视觉。' if outer else
          '内侧从左到右：课程理念、内容亮点与适合人群、学习方向与下一步；以用户已给事实为准。')
        +'建立明确的阅读顺序与字号层级，标题少而有力，正文精简、充足留白，不把所有材料均匀硬塞三栏。'
        '未知的讲师、价格、联系方式和成果不编造，可标注待确认。教育场景采用自然写实人物与真实环境，不用夸张卡通。'
        +('右侧封面板左上方' if outer else '左侧内页面板左上方')
        +'若已有Logo，请保留一处且勿重复；若尚无Logo，可为透明品牌素材留出自然的小块空白，不以大面积白色面板覆盖标题或正文。不要重绘Logo。'
        '三折页为设计概念图片，并非带出血/折页公差的印刷PDF。')


def logo_anchor(text):
    for words,value in [('左上','top_left'),('右上','top_right'),('左下','bottom_left'),('右下','bottom_right')]:
        if words in text:return value
    return None


def logo_geometry(width,height,mode,anchor=None):
    margin=max(4,round(min(width,height)*.03))
    if mode in {'brochure_front','brochure_back'}:
        panel=width/3
        x=round(2*panel)+margin if mode=='brochure_front' else margin
        return x,margin,max(1,round(panel*.36)),max(1,round(height*.065)),margin//3
    maxw,maxh=max(1,round(width*.14)),max(1,round(height*.08))
    return (width-maxw-margin if anchor in {'top_right','bottom_right'} else margin,
            height-maxh-margin if anchor in {'bottom_left','bottom_right'} else margin,maxw,maxh,margin//3)


def overlay_preview_logo(content,logo,mode,anchor=None):
    """Only a contained original logo is scaled; base raster geometry is fixed."""
    inspect_transparent_png(logo)
    with Image.open(io.BytesIO(content)) as source,Image.open(io.BytesIO(logo)) as mark:
        canvas=source.convert('RGBA');mark=mark.convert('RGBA')
        x,y,w,h,padding=logo_geometry(canvas.width,canvas.height,mode,anchor)
        mark.thumbnail((w,h),Image.Resampling.LANCZOS)
        # A local, conservative occupancy check, NOT a universal logo/OCR
        # detector. Any logo, text, photo or uncertain content in the header
        # prevents automatic overlay. Never erase pixels to make space.
        header=canvas.crop((0,0,canvas.width,max(1,round(canvas.height*.16)))).convert('RGB')
        header.thumbnail((720,120),Image.Resampling.LANCZOS)
        if max(ImageStat.Stat(header).stddev)>7:
            return content,{'policy':'preview-transparent-logo-v3','layout_mode':mode,'applied':False,
                'reason':'existing_logo_or_content_preserved','detection':'conservative_header_occupancy_not_semantic_ocr'}
        # Alpha-composite ONLY the original transparent PNG, no plate, mask,
        # white rectangle or background fill. All other pixels stay untouched.
        canvas.alpha_composite(mark,(x+padding,y+padding))
        out=io.BytesIO();canvas.save(out,'PNG')
        return out.getvalue(),{'policy':'preview-transparent-logo-v3','layout_mode':mode,'applied':True,'x':x+padding,'y':y+padding,
            'width':mark.width,'height':mark.height,'logo_width':mark.width,'logo_height':mark.height}


def original_brochure_key(main,actor,key):
    """Read-only repair for our v1/v2 overlays; never rewrite history/files.

    Resolve only this user's exact Generation/Task artifact to its preserved
    Provider original. No arbitrary storage keys or cross-user trace lookup.
    """
    if not actor.tenant_id.startswith('preview-'):return key
    with main.store.connection() as c:
        row=c.execute("select t.run_id from generations g join tasks t on t.id=g.task_id and t.tenant_id=g.tenant_id and t.user_id=g.user_id where g.storage_key=? and g.tenant_id=? and g.user_id=? and g.deleted_at is null and t.agent_id='image-agent' limit 1",
            (key,actor.tenant_id,actor.user_id)).fetchone()
        if not row or not row['run_id']:return key
        trace=c.execute('select payload from run_traces where run_id=? and tenant_id=?',(row['run_id'],actor.tenant_id)).fetchone()
    if not trace:return key
    try:
        for call in json.loads(trace['payload']).get('mcp_calls',[]):
            if call.get('tool')!='image_generation':continue
            for item in call.get('artifacts',[]):
                placement=item.get('brand_placement') or {}
                if item.get('storage_key')!=key or item.get('layout_mode') not in {'brochure_front','brochure_back'} or placement.get('policy') not in {'preview-brand-zone-v1','preview-brand-zone-v2'}:continue
                original=item.get('original_storage_key','');parts=PurePosixPath(original).parts
                if len(parts)==3 and parts[:2]==('generated',actor.tenant_id) and all(p not in {'','.','..'} for p in parts) and '\\' not in original:return original
    except (ValueError,TypeError,AttributeError):pass
    return key
