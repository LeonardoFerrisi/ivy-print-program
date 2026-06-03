"""
Create a properly formatted test image for the Canon Ivy 1.
From the decompiled Canon Mini Print app:
  - Filename: img.jpg
  - Type: image/jpeg
  - Dimensions: 640 x 1616
  - Rotated 180 degrees
  - Quality: 100

Send the output file (img.jpg) via Windows Bluetooth File Transfer.
"""

from PIL import Image, ImageDraw
import sys

# Use provided image or create test pattern
if len(sys.argv) > 1:
    img = Image.open(sys.argv[1]).convert("RGB")
else:
    img = Image.new("RGB", (640, 1616), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 640, 400], fill=(255, 0, 0))
    draw.rectangle([0, 400, 640, 800], fill=(0, 255, 0))
    draw.rectangle([0, 800, 640, 1200], fill=(0, 0, 255))
    draw.rectangle([0, 1200, 640, 1616], fill=(0, 0, 0))

# Scale and crop to 640x1616
w, h = img.size
scale = max(640 / w, 1616 / h)
img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
w2, h2 = img.size
img = img.crop(((w2 - 640) // 2, (h2 - 1616) // 2, (w2 + 640) // 2, (h2 + 1616) // 2))
img = img.rotate(180)

img.save("img.jpg", format="JPEG", quality=100)
print(f"Saved img.jpg ({img.size[0]}x{img.size[1]})")