from app.modules.explorer.folder_notes import product_folder_kind

def test_product_folder_note_names_are_strict():
    assert product_folder_kind("Amazon - B0GD6H8HYJ - Hoodie") == "amazon"
    assert product_folder_kind("Amazon - B0GD6H8HYJ") == "amazon"
    assert product_folder_kind("listing - 4347763062") == "etsy"
    assert product_folder_kind("listing - abc") is None
    assert product_folder_kind("Etsy - 4347763062") is None
