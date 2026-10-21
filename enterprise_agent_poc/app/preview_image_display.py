"""Authenticated, on-demand small display copies. Originals/exports never change."""
import asyncio
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
from PIL import Image,ImageOps


class PreviewImageDisplay:
    def __init__(self,main,illustrations,root):
        self.main=main;self.illustrations=illustrations
        self.root=Path(root)/'display-thumbnails-v1'
        self.slots=asyncio.Semaphore(2)

    def render(self,actor,kind,identifier):
        if not actor.tenant_id.startswith('preview-'):raise PermissionError()
        if kind=='illustrations':
            content,asset=self.illustrations.asset(actor,identifier)
        elif kind=='generations' and re.fullmatch('[a-f0-9-]{36}',identifier):
            with self.main.store.connection() as c:
                row=c.execute('select storage_key from generations where id=? and tenant_id=? and user_id=? and deleted_at is null',
                              (identifier,actor.tenant_id,actor.user_id)).fetchone()
            if not row or not self.main.product_store.readable_generation(actor.tenant_id,actor.user_id,row['storage_key']):raise LookupError()
            content=self.illustrations.storage.get(row['storage_key'])
        else:raise LookupError()
        # Authorization and original integrity are checked BEFORE derived-cache lookup.
        digest=hashlib.sha256(content).hexdigest()
        scope=hashlib.sha256((actor.tenant_id+':'+actor.user_id).encode()).hexdigest()
        path=self.root/scope/(kind+'-'+identifier+'-'+digest+'.webp')
        if path.is_file():return path.read_bytes()
        if len(content)>16*1024*1024:raise ValueError('Image exceeds display decoding limit')
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in {'PNG','JPEG','WEBP'} or max(source.size)>8192 or source.width*source.height>33554432:raise ValueError('Invalid raster')
            image=ImageOps.exif_transpose(source);image.thumbnail((720,720),Image.Resampling.LANCZOS)
            image=image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
            out=io.BytesIO();image.save(out,format='WEBP',quality=80,method=4);data=out.getvalue()
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.display-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(data)
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
        return data
