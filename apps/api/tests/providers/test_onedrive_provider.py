import pytest
from app.providers.source_factory import create_source_provider
from app.providers.microsoft.onedrive import validate_graph_url
from app.providers.microsoft.onedrive_mapper import ONEDRIVE_ROOT_ID,make_item_id,map_item,parse_item_id,root_node

def test_item_id_round_trip_and_sharepoint_rejected():
    value=make_item_id("drive:alpha","item/with+chars")
    assert parse_item_id(value)==("drive:alpha","item/with+chars")
    with pytest.raises(ValueError): parse_item_id("sp:item:YWJj:ZGVm")

@pytest.mark.parametrize(("mime","kind"),[("image/png","image"),("video/mp4","video"),("application/pdf","pdf"),("application/vnd.openxmlformats-officedocument.wordprocessingml.document","document"),("text/plain","other")])
def test_mapper_file_kinds(mime,kind):
    node=map_item({"id":"item","name":"file","file":{"mimeType":mime},"parentReference":{"driveId":"parent-drive","id":"parent"}}, "drive")
    assert node.kind==kind and parse_item_id(node.id)==("drive","item") and parse_item_id(node.parent_id)==("parent-drive","parent")

def test_mapper_folder_and_root():
    assert root_node().id==ONEDRIVE_ROOT_ID
    assert map_item({"id":"folder","name":"folder","folder":{"childCount":1}},"drive").kind=="folder"

def test_graph_cursor_is_strictly_graph_v1():
    assert validate_graph_url("https://graph.microsoft.com/v1.0/drives/x/root/delta?$skiptoken=a")
    for value in ("http://graph.microsoft.com/v1.0/x","https://evil.example/v1.0/x","https://graph.microsoft.com/beta/x"):
        with pytest.raises(ValueError): validate_graph_url(value)

def test_factory_creates_onedrive_adapter():
    assert create_source_provider("onedrive","token").source_type=="onedrive"
