"""Compose the current tenant's original logo, never ask a model to redraw it."""
import io

from PIL import Image

from app.brand_logo import inspect_transparent_png


def overlay_brand_logo(image: bytes, logo: bytes) -> bytes:
    inspect_transparent_png(logo)
    with Image.open(io.BytesIO(image)) as source, Image.open(io.BytesIO(logo)) as mark:
        canvas = source.convert("RGBA")
        original = mark.convert("RGBA")
        # No upscaling; contain preserves geometry, alpha and original colors.
        original.thumbnail((max(1, canvas.width // 5), max(1, canvas.height // 8)), Image.Resampling.LANCZOS)
        margin = max(1, min(canvas.size) // 40)
        canvas.alpha_composite(original, (canvas.width - original.width - margin, margin))
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue()
