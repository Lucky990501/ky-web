"""Local HTTP/browser fixture. Its images are NOT real Provider evidence."""
import argparse
import io
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.preview_image_execution import PreviewImageExecution,AUTHORIZATION_ID,MODE
from scripts.customer_preview import create_preview


async def fixture_image(self,task,plan):
    from PIL import Image
    from app.storage import storage_provider
    self.budget.reserve('image',Decimal('1'))
    output=io.BytesIO();Image.new('RGB',(1024,1024),(30,80,140)).save(output,format='PNG')
    key=f"generated/{task['tenant_id']}/{uuid4().hex}.png"
    storage_provider(self.main.settings).put(key,output.getvalue(),'image/png')
    return {'provider':'FIXTURE_ONLY','model':self.main.settings.image_model_id,
            'results':[{'storage_key':key,'width':1024,'height':1024,'requested_size':plan['size'],'purpose':'FIXTURE_ONLY','dimension_match':True}],
            'delivery':{'status':'completed','requested':1,'generated':1,'failed':0}}


if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);parser.add_argument('--port',type=int,default=18940);args=parser.parse_args()
    PreviewImageExecution.call_image=fixture_image
    app=create_preview(args.data_dir,args.port,args.port+1,local_http=True,
        pricing={'image_pricing_verified':True,'image_request_max_cny':'1'},
        image_authorization={'authorization_id':AUTHORIZATION_ID,'approved':True,'execution_mode':MODE,
            'model':'gpt-image-2.5-sunburst-c','max_provider_calls':2,'request_max_cny':'1'})
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
