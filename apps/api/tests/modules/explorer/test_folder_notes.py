import asyncio

from app.modules.explorer.folder_notes import product_folder_kind, resolve_note_owner_from_nodes
from app.modules.explorer.schema import AssetNode


def folder(folder_id: str, name: str, parent_id: str | None) -> AssetNode:
    return AssetNode(id=folder_id, name=name, kind="folder", mime_type="application/vnd.google-apps.folder", parent_id=parent_id)


def test_product_folder_note_names_are_strict():
    assert product_folder_kind("Amazon - B0GD6H8HYJ - Hoodie") == "amazon"
    assert product_folder_kind("Amazon - B0GD6H8HYJ") == "amazon"
    assert product_folder_kind("listing - 4347763062") == "etsy"
    assert product_folder_kind("listing - abc") is None
    assert product_folder_kind("Etsy - 4347763062") is None


def test_descendants_resolve_the_same_nearest_product_root():
    listing = folder("listing", "listing - 4347763062", "etsy")
    source = folder("source", "Source", "listing")
    final = folder("final", "Final", "source")
    nodes = {item.id: item for item in [listing, source, final]}

    async def get_node(node_id: str) -> AssetNode:
        return nodes[node_id]

    assert asyncio.run(resolve_note_owner_from_nodes(listing, get_node)).id == "listing"
    assert asyncio.run(resolve_note_owner_from_nodes(final, get_node)).id == "listing"


def test_nearest_root_wins_and_ancestors_do_not_inherit():
    amazon = folder("amazon", "Amazon - B012345678", "root")
    etsy = folder("etsy", "Etsy - VienLuna", "root")
    listing = folder("listing", "listing - 4347763062", "etsy")
    source = folder("source", "Source", "listing")
    nodes = {item.id: item for item in [amazon, etsy, listing, source]}

    async def get_node(node_id: str) -> AssetNode:
        return nodes[node_id]

    assert asyncio.run(resolve_note_owner_from_nodes(source, get_node)).id == "listing"
    assert asyncio.run(resolve_note_owner_from_nodes(etsy, get_node)) is None
