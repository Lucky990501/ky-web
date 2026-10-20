"""Preview-only lossless repeated knowledge-body compaction.

Keep every call/result identity, citation and first verbatim source; only replace
subsequent byte-identical source bodies by an explicit earlier-call reference.
"""
import copy
import hashlib
import json
import re


def compact_knowledge_history(payload):
    body=copy.deepcopy(payload);calls={};seen={};removed=0;repeated=0;redundant_tail=0
    for item in body.get('input',[]):
        if not isinstance(item,dict):continue
        if item.get('type')=='function_call':calls[item.get('call_id')]=item.get('name','')
        if item.get('type')!='function_call_output' or not calls.get(item.get('call_id'),'').endswith('knowledge_search'):continue
        raw=item.get('output')
        if not isinstance(raw,str):continue
        # SDK tool output may include a timing wrapper. Parse only a complete
        # trailing array; never interpret arbitrary prose as a tool contract.
        match=re.search(r'(\{\s*"result"\s*:\s*\[.*\]\s*\}|\[\s*\{.*\}\s*\])\s*$',raw,re.S)
        if not match:continue
        try:decoded=json.loads(match.group(1))
        except ValueError:continue
        wrapped=isinstance(decoded,dict) and set(decoded)=={'result'}
        records=decoded['result'] if wrapped else decoded
        if not isinstance(records,list) or not all(isinstance(r,dict) and isinstance(r.get('content'),str) and r.get('file_id') and r.get('chunk_id') for r in records):continue
        changed=False;novel=0
        for record in records:
            key=(str(record['file_id']),str(record['chunk_id']),record['content'])
            if key not in seen:seen[key]=item.get('call_id');novel+=1;continue
            text=record['content'];marker='[Identical source body retained in earlier tool call '+str(seen[key])+', same file_id/chunk_id; SHA256='+hashlib.sha256(text.encode()).hexdigest()+']'
            if len(marker.encode())>=len(text.encode()):continue
            record['content']=marker;removed+=len(text.encode())-len(marker.encode());repeated+=1;changed=True
        if changed:item['output']=raw[:match.start(1)]+json.dumps({'result':records} if wrapped else records,ensure_ascii=False,separators=(',',':'))+raw[match.end(1):]
        # A successful empty result also adds no evidence. Previously it reset
        # the tail to zero, allowing unlimited empty paid searches on a Task.
        redundant_tail=redundant_tail+1 if not novel else 0
    return body,{'repeated_source_bodies':repeated,'source_bytes_saved':removed,'redundant_search_tail':redundant_tail}
