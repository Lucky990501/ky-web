"""Preview Flash final-content policy; no model, budget or business-schema change."""
from copy import deepcopy

LONG_OUTPUT_TOKENS = 16384


def activity_schema_request(body, agent_id):
    if agent_id != 'campaign-agent':
        return False
    from app.activity_plan_runtime import PLAN_SCHEMA
    fmt = (body.get('text') or {}).get('format') or {}
    return fmt.get('type') == 'json_schema' and fmt.get('schema') == PLAN_SCHEMA


def apply_policy(body, agent_id, *, controlled=False):
    """Raise low SDK defaults only for a supported final business-content phase.

    Activity preparation can already contain a full draft before JSON conversion.
    Ordinary auto/text preparation therefore needs the same output space. WeChat can
    answer directly without a tool, so an auto/text turn is a content phase.
    Explicit forced-tool turns and all independent controlled grants are exempt.
    """
    original = body.get('max_output_tokens')
    phase = 'unchanged'
    if not controlled and body.get('model') == 'deepseek-flash':
        if activity_schema_request(body, agent_id):
            phase = 'activity_complete_json'
        elif (agent_id == 'campaign-agent'
              and body.get('tool_choice', 'auto') in ('auto', 'none', None)
              and ((body.get('text') or {}).get('format') or {}).get('type', 'text') == 'text'):
            phase = 'activity_preparation'
        elif (agent_id == 'preview-wechat-agent'
              and body.get('tool_choice', 'auto') in ('auto', 'none', None)
              and ((body.get('text') or {}).get('format') or {}).get('type', 'text') == 'text'):
            phase = 'article_complete_body'
    if phase == 'unchanged':
        return body, {'phase':phase, 'sdk_max_output_tokens':original}
    body = deepcopy(body)
    body['max_output_tokens'] = LONG_OUTPUT_TOKENS
    # Official Flash Responses contract supports low. Keep schema, facts,
    # tools, history and the original business instructions unchanged.
    body['reasoning'] = {**(body.get('reasoning') or {}), 'effort':'low'}
    return body, {'phase':phase, 'sdk_max_output_tokens':original,
                  'effective_max_output_tokens':LONG_OUTPUT_TOKENS,
                  'effective_reasoning_effort':'low'}


def incomplete_message(agent_id):
    name = '活动方案' if agent_id == 'campaign-agent' else '文章'
    return name+'生成未完成，尚未保存为正式结果；不会自动收费重试。'
