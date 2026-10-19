"""Owner-supplied Preview image specification, not Provider certification.

2026-10-19: Owner confirmed image is an array and each allowed size/quality
costs at most CNY1. Existing v2 authorization and durable 20-call budget remain.
No changes to Production defaults, no resize/upscale or automatic paid retry.
"""
import json
import sqlite3
import re

from app.image_request_contract import image_plan, ImageRequestError

MODEL='gpt-image-2.5-sunburst-c'
SIZES=('1024x1024','1536x1024','1024x1536','2048x2048','2048x1152','3840x2160','2160x3840')
QUALITIES=('high','medium','low')
SPEC_ID='owner-preview-image-parameters-20261019-v1'
# Actual QA Task c64988dc-e21a-4cbf-acd7-ebf064a06f75 reached the gateway
# with image:[data-URLs] and received HTTP400. Owner subsequently supplied a
# repeated multipart image example for gpt-image-2-all, a DIFFERENT model.
# Its wire adapter is fixture-tested; current sunburst-c compatibility remains
# unverified. Keep paused until authorized real validation; never auto-retry.
MULTI_REFERENCE_PAUSED=True


def reference_limit():return 1 if MULTI_REFERENCE_PAUSED else 5


def quality_value(value):
    if value is not None and value not in QUALITIES:
        raise ImageRequestError('image_quality_invalid','质量请选择high、medium或low。')
    return value


def preview_plan(message,aspect_ratio='1:1',size=None):
    return image_plan(message,message,aspect_ratio,size,provider_sizes=SIZES)


def provider_error_fields(response):
    """Never store a Provider body/message, token, URL or image data."""
    try:
        if len(response.content)>65536:return {'category':'UNCLASSIFIED'}
        body=response.json();error=body.get('error',{}) if isinstance(body,dict) else {}
        if not isinstance(error,dict):return {'category':'UNCLASSIFIED'}
    except (ValueError,TypeError):return {'category':'UNCLASSIFIED'}
    safe={'category':'UNCLASSIFIED'}
    for key in ('code','type'):
        value=error.get(key)
        if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',value) and not re.search(r'sk-|token|secret|password',value,re.I):safe[key]=value
    if error.get('param') in ('image','image[]','images','size','quality','model','n','output_format','response_format'):safe['param']=error['param']
    message=str(error.get('message','')).lower()
    for pattern,category in [
        (r'(?:unmarshal|array).{0,100}image.{0,50}(?:string|unsupported)|image.{0,50}array.{0,50}(?:invalid|unsupported)','IMAGE_ARRAY_REJECTED'),
        (r'(?:size|resolution).{0,120}(?:invalid|unsupported|not support|not allowed)|(?:unsupported|invalid).{0,30}(?:size|resolution)','SIZE_REJECTED'),
        (r'quality.{0,100}(?:invalid|unsupported|not support)','QUALITY_REJECTED'),
        (r'(?:image|base64|data.url).{0,100}(?:decode|invalid|unsupported|not valid)','REFERENCE_ENCODING_REJECTED'),
        (r'model.{0,100}(?:not found|not available|unsupported)','MODEL_REJECTED'),
    ]:
        if re.search(pattern,message):safe['category']=category;break
    return safe


class PreviewReferenceBindings:
    """Supplement legacy scalar upload binding in the EXISTING Preview ledger.

    The primary upload keeps its original FK; the ordered extra IDs are bound
    to the actual owner-checked Task in the existing submission audit. No new
    table, migration, public URL or caller-supplied tenant identity.
    """
    def __init__(self,products,budget):self.products,self.budget=products,budget
    def submissions(self):
        with sqlite3.connect(self.budget.path) as conn:
            return [item for row in conn.execute('SELECT payload FROM observations ORDER BY rowid')
                    if (item:=json.loads(row[0])).get('event')=='preview_image_submission']
    def claimed(self,principal,attachment_id):
        return any(s.get('tenant_id')==principal.tenant_id and s.get('user_id')==principal.user_id
                   and any(r.get('id')==attachment_id for r in s.get('references',[])) for s in self.submissions())
    def resolve(self,task,requested_ids=None):
        actual=self.products.task_for_worker(task['id'])
        if not actual or actual['agent_id']!='image-agent' or any(actual[k]!=task[k] for k in ('tenant_id','user_id')):
            raise PermissionError('图片任务归属不匹配。')
        submission=next((s for s in self.submissions() if s.get('task_id')==task['id']
                         and s.get('tenant_id')==task['tenant_id'] and s.get('user_id')==task['user_id']),None)
        refs=submission.get('references',[]) if submission else []
        if not submission:
            record=self.products.task_chat_image_attachment(task['id'],task['tenant_id'])
            refs=[{'id':record['id'],'role':'subject'}] if record else []
        ids=[r['id'] for r in refs]
        if len(ids)>5 or len(set(ids))!=len(ids) or requested_ids is not None and requested_ids!=ids:
            raise ValueError('参考图片与当前任务的有序绑定不匹配。')
        records=[]
        for item in refs:
            record=self.products.chat_image_attachment(task['tenant_id'],task['user_id'],item['id'])
            if not record or record.get('task_id') not in {None,task['id']}:
                raise ValueError('参考图片失效或不属于当前任务。')
            # Reject reuse of an extra upload across unrelated accepted Tasks.
            claims=[s['task_id'] for s in self.submissions() if s.get('tenant_id')==task['tenant_id']
                    and s.get('user_id')==task['user_id'] and any(r.get('id')==item['id'] for r in s.get('references',[]))]
            if claims and set(claims)!={task['id']}:raise ValueError('参考图片已绑定其他任务。')
            records.append({**record,'role':item.get('role','subject')})
        return records
    def public(self,task):
        from app.product_store import _chat_image_attachment_view
        return [{**_chat_image_attachment_view(record),'role':record['role']} for record in self.resolve(task)]
