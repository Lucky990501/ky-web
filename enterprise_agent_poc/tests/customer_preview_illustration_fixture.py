"""Local HTTP/SQLite/Chrome harness. ALL model outputs are FIXTURE_ONLY."""
import argparse
import io
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.customer_preview import prepare,create_preview
from app.preview_illustration_provider import IllustrationConfig,IllustrationProvider,validated_image


async def fixture_generate(self,prompt,record_http):
    from PIL import Image
    color='orange' if '师生默契' in prompt or '一起成长' in prompt else 'steelblue'
    out=io.BytesIO();Image.new('RGB',(640,400),color).save(out,format='PNG')
    record_http(200,'FIXTURE_ONLY_NO_PROVIDER');return out.getvalue(),validated_image(out.getvalue())


async def fixture_task(self,task):
    from app import main
    from app.document_generator import ActivityPlanContent
    from app.activity_plan_runtime import render_markdown,result_envelope
    structured=None
    if task['agent_id']=='campaign-agent':
        data=json.loads((Path(__file__).parent/'fixtures/activity_plan_d1.json').read_text(encoding='utf-8'))
        data['activity_items']=data['activity_items'][:2]
        plan=ActivityPlanContent.model_validate(data);text=render_markdown(plan);structured=result_envelope(plan)
    else:
        text='<article><h1>FIXTURE_ONLY 家庭成长</h1><p style="line-height:1.8">'+('家长温和倾听孩子的心声。'*40)+'</p><h2 style="background-color:#fff3c4">倾听孩子</h2><p>保留原正文事实。</p><h2>一起成长</h2><blockquote>保持温和沟通</blockquote><script>window.PREVIEW_XSS=true</script></article>'
    run,conversation=str(uuid4()),task.get('conversation_id') or str(uuid4())
    if not task.get('conversation_id'):main.store.save_conversation(conversation,task['tenant_id'],task['agent_id'],'FIXTURE_ONLY','FIXTURE_ONLY','FIXTURE_ONLY')
    main.store.create_run_trace(run,conversation,task['tenant_id'],task['agent_id'],None,{'fixture':True})
    main.product_store.attach_task_run(task['id'],task['tenant_id'],run,conversation,user_id=task['user_id'])
    main.product_store.set_task(task['id'],task['tenant_id'],'running','generating','FIXTURE_ONLY')
    trace={'fixture':True,'runtime_completed':True,'runtime_status':'completed','final_response_received':True,
           'required_tool_calls_completed':True,'result_persistence_status':'pending','final_result':text,'structured_result':structured,
           'mcp_calls':[{'tool':'enterprise_config_get','status':'completed','output_summary':'FIXTURE_ONLY '+json.dumps(structured,ensure_ascii=False)}]}
    main.store.finish_run_trace(run,'runtime_completed',trace)
    main.product_store.complete_task_success(task,run_id=run,conversation_id=conversation,response=text,trace_payload=trace)


if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);parser.add_argument('--port',type=int,default=18948);parser.add_argument('--unconfigured',action='store_true');args=parser.parse_args()
    prepare(args.data_dir,args.port,args.port+1)
    from app.product_service import TaskService
    from app.preview_image_execution import PreviewImageExecution,AUTHORIZATION_ID,MODE
    from customer_preview_image_fixture import fixture_image
    TaskService.execute=fixture_task
    PreviewImageExecution.call_image=fixture_image
    IllustrationProvider.generate=fixture_generate
    grant={'approved':True,'pricing_verified':True,'authorization_id':'FIXTURE_ONLY_NOT_AUTHORIZATION','model':'qwen-image-3.0',
           'request_max_cny':'0.10','max_total_cny':'5','max_requests':50,'max_images_per_task':5,'automatic_paid_retries':0}
    config=IllustrationConfig() if args.unconfigured else IllustrationConfig(key='FIXTURE_ONLY_NOT_A_KEY',enabled=True)
    app=create_preview(args.data_dir,args.port,args.port+1,local_http=True,
        credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY_NOT_A_KEY'},pricing={'text_pricing_verified':True,'image_pricing_verified':True,'image_request_max_cny':'1'},
        image_authorization={'authorization_id':AUTHORIZATION_ID,'approved':True,'execution_mode':MODE,'model':'gpt-image-2.5-sunburst-c','max_provider_calls':70,'request_max_cny':'1'},
        illustration_config=config,illustration_authorization=grant)
    from app import main
    assert main.settings.database_url=='sqlite:///'+str(Path(args.data_dir).resolve()/'preview.sqlite')
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
