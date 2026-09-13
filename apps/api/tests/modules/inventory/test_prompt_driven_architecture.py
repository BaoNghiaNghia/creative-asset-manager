from pathlib import Path


def test_workbook_role_terms_are_prompt_content_not_backend_control_flow():
    root = Path(__file__).resolve().parents[3] / "app/modules/inventory"
    guarded_code = [
        root / "daily/carry_forward.py",
        root / "daily/carry_forward_planner.py",
        root / "daily_sheet/agent_v4/tools.py",
    ]
    forbidden = ("KHO PHA", "PHÒNG PHA", "Tồn đầu", "Tồn cuối", "Tồn kho", "Nguyên", "Lẻ", "ca sáng", "ca tối")
    text = "\n".join(path.read_text() for path in guarded_code)
    assert not any(term.casefold() in text.casefold() for term in forbidden)


def test_builtin_prompts_keep_workbook_interpretation_editable():
    prompt_file = (Path(__file__).resolve().parents[3] / "app/modules/inventory/daily_sheet/prompts.py").read_text()
    assert "Do not assume fixed sheet positions" in prompt_file
    assert "metadata, headers, merged cells" in prompt_file
