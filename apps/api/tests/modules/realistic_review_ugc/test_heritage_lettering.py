"""Heritage lettering style contract: job-pinned, safe, and distinct per slot."""
import pytest
from app.modules.realistic_review_ugc.heritage_lettering import (
    HERITAGE_LAYOUTS, STYLE_MODES, style_guidance, validate_style_mode,
)
from app.modules.realistic_review_ugc.creative_v3_generation import prompt_for_slot
from tests.modules.realistic_review_ugc.test_creative_v3_generation import plan


def test_heritage_is_distinct_across_nine_slots():
    assert len(HERITAGE_LAYOUTS) == 9
    prompts = [style_guidance("heritage", i) for i in range(1, 10)]
    assert len(set(prompts)) == 9
    assert all("NEVER trace" in value for value in prompts)
    assert "Script" in prompts[2]
    assert "Collegiate" in prompts[5]


def test_mode_validation_rejects_untrusted_or_unknown_style():
    assert validate_style_mode("heritage") == "heritage"
    assert STYLE_MODES == ("balanced", "heritage", "experimental")
    for value in ("", "Heritage", "../../etc/passwd", "legacy"):
        with pytest.raises(ValueError):
            validate_style_mode(value)
    with pytest.raises(ValueError):
        style_guidance("heritage", 10)


def test_heritage_prompt_stays_text_only_and_pins_style():
    data = plan("ONE NATION UNDER GOD")
    prompt = prompt_for_slot(data, 4, design_style="heritage")
    assert "ONE NATION UNDER GOD" in prompt
    assert "HERITAGE STYLE PACK" in prompt
    assert "Editorial Script Overlay" in prompt
    assert "NEVER trace" in prompt
    assert "HERITAGE STYLE PACK" not in prompt_for_slot(data, 4)
    assert "EXPERIMENTAL MODE" in prompt_for_slot(data, 4, design_style="experimental")
