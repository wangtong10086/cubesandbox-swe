"""Template naming helpers for SWE-INFINITE Docker images."""

from __future__ import annotations

import hashlib
import re


def slugify_image_tag(tag: str) -> str:
    """Return the normalized CubeSandbox template slug for an image tag."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", tag).strip("-").lower()
    return re.sub(r"-+", "-", slug)


def template_id_for(image: str, *, max_base_len: int = 95) -> str:
    """Return the stable template id used by the SWE image preparation flow."""
    if ":" not in image:
        raise ValueError(f"image must include a tag: {image!r}")

    tag = image.rsplit(":", 1)[1]
    base = f"swe-{slugify_image_tag(tag)}"
    if len(base) > max_base_len:
        base = base[:max_base_len].rstrip("-")

    # Historical compatibility: the first exploratory template was created
    # before the digest suffix was added and is referenced by existing data.
    if base == "swe-asottile-dead-cf792cdc-199":
        return base

    digest = hashlib.sha1(image.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"
