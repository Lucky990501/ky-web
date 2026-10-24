"""Zero-cost reproduction of sky-background logo skips and plan-intent loss."""
import io
from PIL import Image,ImageDraw
from app.preview_image_layout import overlay_preview_logo
from app.activity_brief import with_user_context,assess_activity,CONTEXT_PREFIX
from app.activity_plan_runtime import is_full_activity_plan


def raster(image):
    out=io.BytesIO();image.save(out,'PNG');return out.getvalue()


def test_smooth_sky_and_unrelated_title_do_not_suppress_transparent_logo():
    canvas=Image.new('RGB',(600,1000));draw=ImageDraw.Draw(canvas)
    for y in range(1000):draw.line((0,y,599,y),fill=(90+y//10,160+y//16,210+y//25))
    draw.text((60,230),'FIXTURE TITLE',fill='black')
    logo=Image.new('RGBA',(240,80),(0,0,0,0));ImageDraw.Draw(logo).rectangle((10,15,220,65),fill=(0,90,170,255))
    raw=raster(canvas);result,meta=overlay_preview_logo(raw,raster(logo),None)
    assert meta['applied'] and meta['x']>400
    with Image.open(io.BytesIO(result)) as composed:
        assert composed.size==canvas.size
        assert composed.getpixel((meta['x'],meta['y']))[:3]==canvas.getpixel((meta['x'],meta['y']))
    # Reapplying to the same branded raster must not add another copy.
    again,second=overlay_preview_logo(result,raster(logo),None)
    assert not second['applied'] and again==result


def test_planning_case_and_short_followup_retain_formal_contract(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    first='写一个元旦节的策划案。活动内容：品牌宣传；时间：1月1日；地点：线下门店；对象：家长和学生。'
    assert is_full_activity_plan('campaign-agent',first)
    natural='写一个元旦节的策划案，面向家长和学生，元旦节当天，线下门店，品牌宣传活动。'
    assert is_full_activity_plan('campaign-agent',natural) and assess_activity(natural)['ready']
    messages=[{'role':'user','content':first},{'role':'assistant','content':'FIXTURE 补充说明。'}]
    current='请直接生成完整方案。'
    context=with_user_context(current,messages)
    assert context.startswith(CONTEXT_PREFIX) and assess_activity(context)['ready']
    assert with_user_context(current,[])==current
    small='给我三个活动标题'
    assert with_user_context(small,messages)==small and not is_full_activity_plan('campaign-agent',small)
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE')
    assert with_user_context(current,messages)==current
