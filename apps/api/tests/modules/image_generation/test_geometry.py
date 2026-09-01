import pytest

from app.modules.image_generation.geometry import calculate_square_geometry


def test_landscape_is_centered_without_crop_or_stretch():
    result = calculate_square_geometry(1600, 900, 2048)
    assert (result.normalized_width, result.normalized_height) == (2048, 1152)
    assert (result.left, result.top, result.right, result.bottom) == (0, 448, 0, 448)


def test_portrait_is_centered_without_crop_or_stretch():
    result = calculate_square_geometry(900, 1600, 1024)
    assert (result.normalized_width, result.normalized_height) == (576, 1024)
    assert (result.left, result.top, result.right, result.bottom) == (224, 0, 224, 0)


def test_odd_remaining_pixel_is_assigned_to_second_side():
    result = calculate_square_geometry(3, 2, 1024)
    assert result.normalized_height == 683
    assert (result.top, result.bottom) == (170, 171)


def test_square_fills_target_and_marks_fast_path():
    result = calculate_square_geometry(512, 512, 2048)
    assert result.already_square
    assert (result.normalized_width, result.normalized_height) == (2048, 2048)
    assert (result.left, result.top, result.right, result.bottom) == (0, 0, 0, 0)


@pytest.mark.parametrize("target", [1024, 2048])
def test_supported_targets(target):
    result = calculate_square_geometry(10, 5, target)
    assert result.normalized_width == target
    assert result.normalized_height == target // 2


@pytest.mark.parametrize("width,height", [(0, 1), (1, 0), (-1, 2), (2, -1)])
def test_invalid_dimensions(width, height):
    with pytest.raises(ValueError, match="image_generation_invalid_dimensions"):
        calculate_square_geometry(width, height, 1024)


def test_invalid_target():
    with pytest.raises(ValueError, match="image_generation_target_size_unsupported"):
        calculate_square_geometry(10, 10, 512)
