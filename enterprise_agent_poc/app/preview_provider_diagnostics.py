"""Allowlisted Preview transport diagnostics; never retain Provider raw bodies."""
import re
from collections import Counter

MACHINE_CODES=frozenset({'invalid_request_error','invalid_request','invalid_parameter','unsupported_parameter',
    'model_not_found','invalid_model','invalid_api_key','authentication_error','insufficient_balance',
    'rate_limit_exceeded','server_error','context_length_exceeded','bad_request','invalid_input'})
PARAMS=frozenset({'model','input','instructions','reasoning','reasoning.effort','text','text.format','text.format.type',
    'text.format.schema','tools','tool_choice','max_output_tokens','stream','temperature','top_p'})


def provider_error_diagnostic(status,value):
    result={'http_status':status if type(status) is int else None,'code':'UNKNOWN','type':'UNKNOWN','param':'UNKNOWN','category':'provider_request_rejected'}
    if not isinstance(value,dict):return result
    error=value.get('error',value)
    if not isinstance(error,dict):return result
    for field in ['code','type']:
        if isinstance(error.get(field),str) and error[field] in MACHINE_CODES:result[field]=error[field]
    if isinstance(error.get('param'),str) and error['param'] in PARAMS:result['param']=error['param']
    # Read transiently for categories only; not returned or persisted.
    message=str(error.get('message','')).lower()
    patterns=[('model_identity',r'model.{0,50}(?:not found|does not exist|not support|invalid)'),
        ('output_token_parameter',r'max_output_tokens|max.tokens'),
        ('reasoning_parameter',r'reasoning|thinking'),('tool_choice_parameter',r'tool_choice'),
        ('json_schema_parameter',r'json_schema|text\.format'),('balance_or_quota',r'insufficient.*(?:balance|quota)|余额|额度'),
        ('authentication',r'authentication|api.?key|unauthorized')]
    for category,pattern in patterns:
        if re.search(pattern,message):result['category']=category;break
    return result


def request_diagnostic(body):
    reasoning=body.get('reasoning') or {};format_=(body.get('text') or {}).get('format') or {}
    choice=body.get('tool_choice','auto');choice=choice.get('type') if isinstance(choice,dict) else choice
    return {'model':body.get('model') if body.get('model') in {'deepseek-v4-pro','deepseek-flash'} else 'UNKNOWN',
        'endpoint':'https://api.deepseek.com/responses','stream':body.get('stream') is True,
        'max_output_tokens':body.get('max_output_tokens') if type(body.get('max_output_tokens')) is int else None,
        'reasoning_effort':reasoning.get('effort') if reasoning.get('effort') in {'none','low','high','max','minimal','medium','xhigh'} else 'DEFAULT_OR_UNKNOWN',
        'text_format':format_.get('type') if format_.get('type') in {'text','json_object','json_schema'} else 'DEFAULT_OR_UNKNOWN',
        'tool_choice':choice if choice in {'auto','none','required','function'} else 'UNKNOWN',
        'tool_count':len(body.get('tools',[])),
        'input_types':dict(Counter(item.get('type','message') for item in body.get('input',[]) if isinstance(item,dict)))}
