from pathlib import Path
from PIL import Image, ImageDraw
root=Path("apps/scout-manager-desktop/src-tauri/icons")
root.mkdir(parents=True, exist_ok=True)
im=Image.new("RGBA",(256,256),(0,0,0,0))
d=ImageDraw.Draw(im)
d.rounded_rectangle((12,12,244,244),radius=56,fill=(30,100,232),outline=(119,166,255),width=3)
d.rounded_rectangle((42,54,214,202),radius=33,fill=(12,43,117))
d.rounded_rectangle((64,83,192,103),radius=10,fill=(232,241,255))
d.rounded_rectangle((64,119,144,139),radius=10,fill=(232,241,255))
d.ellipse((166,118,194,146),fill=(68,223,158))
d.arc((148,153,199,193),0,290,fill=(68,223,158),width=12)
for sz in (32,128):
    im.resize((sz,sz),Image.Resampling.LANCZOS).save(root/f"{sz}x{sz}.png")
im.save(root/"icon.ico",format="ICO",sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("Icon generated for Tauri NSIS.")
