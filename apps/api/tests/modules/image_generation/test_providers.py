from app.modules.image_generation.providers import (
    GEMINI_SQUARE_EXPANSION_INSTRUCTION,
    gemini_expansion_prompt,
)


def test_gemini_expansion_prompt_uses_only_base_instruction_without_preference():
    assert gemini_expansion_prompt(None) == GEMINI_SQUARE_EXPANSION_INSTRUCTION
    assert gemini_expansion_prompt("") == GEMINI_SQUARE_EXPANSION_INSTRUCTION
    assert gemini_expansion_prompt("   ") == GEMINI_SQUARE_EXPANSION_INSTRUCTION


def test_gemini_expansion_prompt_appends_trimmed_user_preference():
    assert gemini_expansion_prompt("  keep the beach wider  ") == (
        GEMINI_SQUARE_EXPANSION_INSTRUCTION
        + "\n\nUser preference:\nkeep the beach wider"
    )
