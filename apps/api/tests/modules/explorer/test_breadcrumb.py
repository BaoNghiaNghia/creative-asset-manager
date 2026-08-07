from app.modules.explorer.breadcrumb import resolve_breadcrumb

def test_nested_breadcrumb_stops_at_root():
    folders = {
        "root": {"name": "Desify - Image & Video Assets", "parent_id": None},
        "etsy": {"name": "Etsy - Pasimax", "parent_id": "root"},
        "listing": {"name": "listing - 4344786926", "parent_id": "etsy"},
    }
    assert resolve_breadcrumb(item_id="file", parent_id="listing", folders=folders, source_root_id="root") == [
        {"id": "root", "name": "Desify - Image & Video Assets"},
        {"id": "etsy", "name": "Etsy - Pasimax"},
        {"id": "listing", "name": "listing - 4344786926"},
    ]

def test_root_file_and_incomplete_or_cycle_are_safe():
    folders = {"root": {"name": "Root", "parent_id": None}}
    assert resolve_breadcrumb(item_id="file", parent_id="root", folders=folders, source_root_id="root") == [{"id": "root", "name": "Root"}]
    assert resolve_breadcrumb(item_id="file", parent_id="missing", folders=folders, source_root_id="root") == []
    cyclic = {"a": {"name": "A", "parent_id": "b"}, "b": {"name": "B", "parent_id": "a"}}
    assert resolve_breadcrumb(item_id="file", parent_id="a", folders=cyclic) == []

def test_viewer_root_prevents_ancestors_above_scope():
    folders = {"root": {"name": "Root", "parent_id": None}, "assigned": {"name": "Assigned", "parent_id": "root"}, "child": {"name": "Child", "parent_id": "assigned"}}
    assert resolve_breadcrumb(item_id="file", parent_id="child", folders=folders, source_root_id="root", permitted_root_ids={"assigned"}) == [{"id": "assigned", "name": "Assigned"}, {"id": "child", "name": "Child"}]
