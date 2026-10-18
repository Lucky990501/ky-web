"""Read-only supplier pricing lookup; no generation or secret output."""
import json
from pathlib import Path
import httpx

def main():
    rows = {}
    for line in Path('/opt/enterprise-agent-workbench/shared/enterprise-agent.env').read_text().splitlines():
        key, separator, value = line.strip().partition('=')
        if separator and key in {'N1N_IMAGE_API_KEY', 'GATEWAY_API_TOKEN'}:
            rows[key] = value.strip().strip('"').strip("'")
    for endpoint in ['https://llm-api.net/api/pricing', 'https://llm-api.net/api/status', 'https://llm-api.net/v1/models']:
        try:
            response = httpx.get(endpoint, headers={'Authorization':'Bearer '+(rows.get('N1N_IMAGE_API_KEY') or rows.get('GATEWAY_API_TOKEN',''))}, timeout=15)
            result={'endpoint':endpoint,'http_status':response.status_code}
            if response.status_code == 200:
                body=response.json()
                if isinstance(body,dict) and '/api/pricing' in endpoint:
                    result['pricing_metadata']={k:({name:value for name,value in v.items() if name.startswith('Gpt-Image-')} if isinstance(v,dict) else v) for k,v in body.items() if k in {'group_ratio','usable_group','success','currency','dollar_exchange_rate','quota_per_unit'}}
                if isinstance(body,dict) and '/api/status' in endpoint:
                    result['currency_metadata']={k:v for k,v in body.get('data',{}).items() if any(s in k for s in ['currency','exchange','quota_per_unit','dollar'])}
                data=body.get('data',body) if isinstance(body,dict) else body
                if isinstance(data,list):
                    result['matching_model_records']=[{k:v for k,v in item.items() if k in {'model_name','id','model_price','quota_type','enable_groups','available'}} for item in data if isinstance(item,dict) and any(str(v)=='gpt-image-2.5-sunburst-c' for v in item.values())]
            print(json.dumps(result,ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'endpoint':endpoint,'error_type':type(exc).__name__}))

if __name__=='__main__': main()
