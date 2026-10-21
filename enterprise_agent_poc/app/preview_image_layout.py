"""Small Preview-only brochure semantics and original-Logo brand zones."""
import io
import re
from PIL import Image
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
        +'是品牌组件安全区：该面板顶部16%高度内保持干净浅色留白，不放适合人群、课程信息、人物或标题；Logo由服务端叠加原始资产，不要重绘Logo。'
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
        # An explicit brand component, inside the area reserved in the prompt.
        panel=Image.new('RGBA',(mark.width+padding*2,mark.height+padding*2),(255,255,255,245))
        panel.alpha_composite(mark,(padding,padding));canvas.alpha_composite(panel,(x,y))
        out=io.BytesIO();canvas.save(out,'PNG')
        return out.getvalue(),{'policy':'preview-brand-zone-v1','layout_mode':mode,'x':x,'y':y,
            'width':panel.width,'height':panel.height,'logo_width':mark.width,'logo_height':mark.height}
