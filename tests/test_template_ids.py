from __future__ import annotations

import pytest

from cubesandbox_swe.template_ids import slugify_image_tag, template_id_for


def test_slugify_image_tag() -> None:
    assert slugify_image_tag("rubocop.rubocop-b9a290d2-7660") == "rubocop-rubocop-b9a290d2-7660"


def test_template_id_is_stable_with_digest() -> None:
    image = "affinefoundation/swe_infinite_images:rubocop.rubocop-6edb065b-11824"
    assert template_id_for(image) == "swe-rubocop-rubocop-6edb065b-11824-918d407d"


def test_template_id_keeps_historical_first_template() -> None:
    image = "affinefoundation/swe_infinite_images:asottile.dead-cf792cdc-199"
    assert template_id_for(image) == "swe-asottile-dead-cf792cdc-199"


def test_template_id_requires_tag() -> None:
    with pytest.raises(ValueError, match="tag"):
        template_id_for("affinefoundation/swe_infinite_images")
