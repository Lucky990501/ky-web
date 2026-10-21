"""Safe Preview image admission errors; never expose raw internal exceptions."""
from app.image_request_contract import ImageRequestError,INPUT_ERRORS
from app.preview_budget import PreviewBudgetExceeded
from app.preview_task_admission import PreviewTaskCapacity

MESSAGES={
 'preview_image_side_ambiguous':'请确认要补正面还是反面，我会保留当前项目已有图片，不从头重做。',
 'preview_image_count_ambiguous':'需求中给出了不同的总张数，请确认最终一共需要几张。',
 'preview_image_count_limit':'每次最多生成5张图片，请将超过5张的需求分批提交。',
 'preview_image_size_ambiguous':'需求中有互相矛盾的尺寸或比例，请保留最终要求。',
 'preview_image_context_missing':'请先在需要续作的图片项目中打开上一轮结果，再发送修改或补图要求。',
 'preview_image_context_busy':'当前项目仍在生成中，请等待本轮完成，避免重复生成和扣费。',
 'preview_image_context_long':'当前项目累计要求过长，请简要确认要保留的核心要求；没有提交新请求。',
 'preview_image_context_changed':'当前项目已有更新，请重新发送本轮要求；没有提交新请求。',
 'preview_image_outcome_unknown':'所选图片上次请求结果尚未确认，不能直接补发；请保留诊断ID核实，没有再次调用。',
 'preview_image_nothing_missing':'所选图片已生成或当前没有缺失项。如需不同效果，请明确提出再来一个新版本。',
 'PREVIEW_INSUFFICIENT_CREDIT':'当前体验企业的演示积分不足，未创建图片任务；请联系Owner补充演示积分。模型费用额度与积分独立。',
 'IMAGE_REFERENCE_INVALID':'参考图片不存在、已失效或已用于其他任务，请重新选择有权限的参考图片。',
 'IMAGE_EDIT_BASE_MISSING':'编辑基图不存在或无权访问，请在当前项目重新选择基图。',
 'IMAGE_EDIT_BASE_CONFLICT':'请选择当前会话中的一张编辑基图，或上传参考图片，不要同时提交。',
 'IMAGE_CONTEXT_NOT_READY':'企业知识尚未就绪或没有可核验资料，请先处理知识文件，或取消使用企业知识。',
 'IMAGE_CONTEXT_BUDGET_EXHAUSTED':'企业知识检索额度不足，未提交图片任务；可取消使用企业知识，或联系Owner追加检索授权。',
 'IMAGE_BUDGET_EXHAUSTED':'图片模型体验额度不足，没有发起新请求。',
 'IMAGE_AUTHORIZATION_REQUIRED':'当前访客或图片智能体未取得本次操作权限，没有发起生成请求。',
 'IMAGE_REQUEST_INVALID':'图片请求格式无效，请检查需求、参考图片和费用确认；没有发起生成请求。',
 'preview_edit_base_required':'请先点击当前项目图片的“基于此图继续修改”，或上传参考图；未提交新的生图任务。',
 'preview_multi_reference_pending':'当前多参考图编辑尚未启用，请先使用一张参考图；没有丢弃或提交额外图片。',
}


def image_admission_error(error):
    if isinstance(error,PreviewTaskCapacity):return 429,{'error_code':'PREVIEW_TASK_CAPACITY','user_message':str(error)}
    if str(error)=='insufficient_credit':code='PREVIEW_INSUFFICIENT_CREDIT';status=402
    elif isinstance(error,PreviewBudgetExceeded):
        code='IMAGE_CONTEXT_BUDGET_EXHAUSTED' if error.code=='PREVIEW_EMBEDDING_BUDGET_EXHAUSTED' else 'IMAGE_BUDGET_EXHAUSTED';status=429
    elif isinstance(error,ImageRequestError):
        code=error.code;status=404 if code=='IMAGE_EDIT_BASE_MISSING' else 422
        text=MESSAGES.get(code) or INPUT_ERRORS.get(code) or ('质量请选择high、medium或low。' if code=='image_quality_invalid' else None)
        if text:return status,{'error_code':code,'user_message':text}
        code='IMAGE_REQUEST_INVALID'
    elif isinstance(error,PermissionError):code='IMAGE_AUTHORIZATION_REQUIRED';status=403
    elif isinstance(error,LookupError):code='IMAGE_EDIT_BASE_MISSING';status=404
    elif str(error) in {'当前企业没有已就绪知识切片，请先上传处理资料，或取消使用知识。','知识检索费用边界未就绪。','企业知识的真实语义检索尚未就绪，未提交图片任务。'}:
        code='IMAGE_CONTEXT_NOT_READY';status=409
    else:code='IMAGE_REQUEST_INVALID';status=422
    return status,{'error_code':code,'user_message':MESSAGES[code]}
