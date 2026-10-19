"""Make new geometric QA inputs, never read or reuse a customer portrait."""
from pathlib import Path
import sys
from PIL import Image,ImageDraw

target=Path(sys.argv[1]);target.mkdir(parents=True,exist_ok=True)
for name,background,color,shape in [('square.png','white','#2f68ed','square'),('circle.png','white','#eebf30','circle')]:
    image=Image.new('RGB',(256,256),background);draw=ImageDraw.Draw(image)
    if shape=='square':draw.rectangle((48,48,208,208),fill=color)
    else:draw.ellipse((48,48,208,208),fill=color)
    image.save(target/name,format='PNG')
print('Created two new geometric QA fixtures; no customer image used.')
