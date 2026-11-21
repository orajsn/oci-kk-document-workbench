"""Deterministic image preparation; no generative image repair or invented text."""
from io import BytesIO
from pathlib import Path
from .errors import AdapterError

def prepare_image(path: Path | bytes, *, max_bytes=10_000_000, max_pixels=20_000_000,
                  max_edge=2400, crop=None, rotate=0):
    """Return JPEG bytes and safe metadata. Limits are application policy, not OCI limits.

    Optional crop is an explicitly supplied pixel rectangle after EXIF transpose.
    Automatic document/collage localization is NOT implemented by this function.
    """
    raw = path if isinstance(path, bytes) else Path(path).read_bytes()
    if len(raw) > max_bytes:
        raise AdapterError("IMAGE_TOO_LARGE")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise AdapterError("IMAGE_DEPENDENCY_MISSING") from exc
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in ("JPEG", "PNG"):
                raise AdapterError("IMAGE_FORMAT_UNSUPPORTED")
            width, height = source.size
            if width * height > max_pixels:
                raise AdapterError("IMAGE_PIXEL_LIMIT")
            orientation = source.getexif().get(274, 1)
            picture = ImageOps.exif_transpose(source).convert("RGB")
            transformed_size = picture.size
            if crop is not None:
                if (not isinstance(crop, (list, tuple)) or len(crop) != 4
                    or any(type(x) is not int for x in crop)):
                    raise AdapterError("CROP_INVALID")
                l, t, r, b = crop
                if not (0 <= l < r <= picture.width and 0 <= t < b <= picture.height):
                    raise AdapterError("CROP_OUT_OF_BOUNDS")
                picture = picture.crop((l, t, r, b))
            if rotate not in (0, 90, 180, 270):
                raise AdapterError("ROTATION_INVALID")
            if rotate:
                picture = picture.rotate(rotate, expand=True)
            if not 256 <= max_edge <= 8192:
                raise AdapterError("IMAGE_EDGE_LIMIT_INVALID")
            picture.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            output = BytesIO()
            picture.save(output, format="JPEG", quality=95)
            return output.getvalue(), {"original_size": [width, height],
                "exif_orientation": orientation, "oriented_size": list(transformed_size),
                "sent_size": list(picture.size), "crop_applied": crop is not None,
                "rotation_degrees": rotate, "automatic_localization": False,
                "preprocessing_version": "1.0"}
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError("IMAGE_DECODE_FAILED") from exc
