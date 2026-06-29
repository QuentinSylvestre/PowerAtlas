"""Tests for tray icon creation."""

from unittest.mock import patch

from PIL import Image

from power_atlas.tray import _create_icon


def test_create_icon_loads_ico():
    img = _create_icon()
    assert isinstance(img, Image.Image)
    assert img.mode == "RGBA"
    assert img.size[0] >= 16


def test_create_icon_fallback_on_missing_file():
    with patch("power_atlas.tray.Image.open", side_effect=OSError("not found")), \
         patch("power_atlas.tray.log") as mock_log:
        img = _create_icon()
    assert isinstance(img, Image.Image)
    assert img.size == (16, 16)
    assert img.mode == "RGBA"
    assert mock_log.warning.called
