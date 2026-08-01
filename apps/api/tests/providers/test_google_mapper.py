import unittest

from app.providers.google.mapper import map_drive_file


class GoogleDriveMapperTest(unittest.TestCase):
    def test_octet_stream_avif_is_mapped_as_an_image(self):
        item = map_drive_file(
            {
                "id": "drive-file-id",
                "name": "PHOTO.AVIF",
                "mimeType": "application/octet-stream",
            }
        )

        self.assertEqual(item.mime_type, "image/avif")
        self.assertEqual(item.kind, "image")

    def test_folder_mime_is_preserved(self):
        item = map_drive_file(
            {
                "id": "folder-id",
                "name": "Folder",
                "mimeType": "application/vnd.google-apps.folder",
            }
        )

        self.assertEqual(item.kind, "folder")
        self.assertTrue(item.has_children)
