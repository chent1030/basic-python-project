from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.projects.construction_plan_review.assets import render_visual_assets, resolve_preview_files
from app.projects.construction_plan_review.models import BoundingBox
from app.projects.construction_plan_review.visual_checks import RedSealColorVerifier


def _png(color: str = "white") -> bytes:
    image = Image.new("RGB", (120, 80), color)
    if color == "white":
        ImageDraw.Draw(image).rectangle((30, 20, 90, 60), fill="red")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_resolve_uses_only_active_preview_urls() -> None:
    entity = {
        "constructionProgrammeFileInfoList": [
            {"id": 1, "fileKey": "a", "fileName": "a.png", "s3PreviewFileUrl": "https://s3/a"},
            {
                "id": 2,
                "fileKey": "b",
                "fileName": "b.png",
                "s3PreviewFileUrl": "https://s3/b",
                "isDelete": True,
            },
            {"id": 3, "fileKey": "c", "fileName": "c.png", "s3PreviewFileUrl": ""},
        ],
        "constructionTechDiscloseFileInfoList": [
            {"id": 99, "s3PreviewFileUrl": "https://s3/ignored"}
        ],
    }
    files = resolve_preview_files(entity)
    assert [item.file_id for item in files] == ["a"]
    assert files[0].preview_url == "https://s3/a"


def test_render_image_assigns_internal_asset_index() -> None:
    assets = render_visual_assets(
        _png(),
        resolve_preview_files(
            {"constructionProgrammeFileInfoList": [{"id": 1, "s3PreviewFileUrl": "https://s3/a"}]}
        )[0],
    )
    assert len(assets) == 1
    assert assets[0].asset_index == 0
    assert assets[0].asset_id


def test_red_color_is_verified_without_llm() -> None:
    result = RedSealColorVerifier().verify(_png(), BoundingBox(x1=20, y1=10, x2=100, y2=70))
    assert result.is_red is True
    assert result.red_ratio > 0.08
    assert result.method == "hsv_pixel_ratio"


def test_non_red_color_is_not_passed() -> None:
    result = RedSealColorVerifier().verify(_png("blue"))
    assert result.is_red is False
