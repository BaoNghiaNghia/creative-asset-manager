from scout import allowed_image, allowed_pin, normalize_candidates


def test_url_allowlists():
    assert allowed_pin("https://www.pinterest.com/pin/123/")
    assert allowed_pin("https://pinterest.com/pin/abc/")
    assert not allowed_pin("https://www.pinterest.com/search/pins/?q=abc")
    assert not allowed_pin("https://evil.example/pin/123/")
    assert allowed_image("https://i.pinimg.com/736x/a/b/c.jpg")
    assert not allowed_image("https://example.com/image.jpg")


def test_normalize_candidates_filters_and_dedupes():
    rows = [
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/a.jpg",
            "alt_text": "one",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/a.jpg",
            "alt_text": "duplicate",
        },
        {
            "pin_url": "https://evil.example/pin/9/",
            "image_url": "https://i.pinimg.com/b.jpg",
        },
    ]
    result = normalize_candidates(rows)
    assert len(result) == 1
    assert result[0].alt_text == "one"
