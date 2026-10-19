"""Local HTTP fixture: model/image outputs are explicitly FIXTURE_ONLY."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from customer_preview_image_fixture import fixture_image
from app.preview_image_execution import PreviewImageExecution,AUTHORIZATION_ID,MODE
from scripts.customer_preview import create_preview, prepare


async def fixture_article(self,task):
    from app import main
    from app.preview_article_references import PreviewArticleReferences
    from app.preview_budget import PreviewBudget
    refs=PreviewArticleReferences(main,PreviewBudget(main.settings.data_dir))
    reference=refs.for_task(task)
    # Tests assert the attachment is bound BEFORE the executor can run.
    text='FIXTURE_ONLY 已读取对标结构：'+(reference['filename'] if reference else '无附件')
    run,conversation=str(uuid4()),task.get('conversation_id') or str(uuid4())
    if not task.get('conversation_id'):main.store.save_conversation(conversation,task['tenant_id'],task['agent_id'],'FIXTURE_ONLY','FIXTURE_ONLY','FIXTURE_ONLY')
    main.store.create_run_trace(run,conversation,task['tenant_id'],task['agent_id'],None,{'fixture':True})
    main.product_store.attach_task_run(task['id'],task['tenant_id'],run,conversation,user_id=task['user_id'])
    main.product_store.set_task(task['id'],task['tenant_id'],'running','generating','FIXTURE_ONLY')
    trace={'fixture':True,'runtime_completed':True,'runtime_status':'completed','final_response_received':True,'required_tool_calls_completed':True,
           'result_persistence_status':'pending','final_result':text,'mcp_calls':[]}
    main.store.finish_run_trace(run,'runtime_completed',trace)
    main.product_store.complete_task_success(task,run_id=run,conversation_id=conversation,response=text,trace_payload=trace)


if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);parser.add_argument('--port',type=int,default=18944);args=parser.parse_args()
    # Settings has a module-level singleton: prepare BEFORE importing any
    # module that imports settings, never touch the default worktree database.
    prepare(args.data_dir,args.port,args.port+1)
    import app.preview_image_parameters as parameters
    parameters.MULTI_REFERENCE_PAUSED=False  # Local Fixture tests, NEVER the customer launcher.
    from app.product_service import TaskService
    TaskService.execute=fixture_article
    PreviewImageExecution.call_image=fixture_image
    app=create_preview(args.data_dir,args.port,args.port+1,local_http=True,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY_NOT_A_KEY'},
        pricing={'image_pricing_verified':True,'image_request_max_cny':'1','text_pricing_verified':True},
        image_authorization={'authorization_id':AUTHORIZATION_ID,'approved':True,'execution_mode':MODE,
            'model':'gpt-image-2.5-sunburst-c','max_provider_calls':70,'request_max_cny':'1'})
    from app import main
    assert main.settings.database_url=='sqlite:///'+str(Path(args.data_dir).resolve()/'preview.sqlite')
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
