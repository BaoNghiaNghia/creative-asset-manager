import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.authorization.folder_scope import ViewerFolderScopeModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.explorer.router import viewer_bootstrap


class ViewerBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, class_=Session, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    @staticmethod
    def principal(*, tenant: str = "tenant-a", roles: frozenset[str] = frozenset({"viewer"})) -> CurrentPrincipal:
        return CurrentPrincipal(
            user_id="user-a",
            active_tenant_id=tenant,
            membership_id="membership-a",
            external_identity=None,
            effective_roles=roles,
            effective_permissions=frozenset({"assets.read", "search.read"}),
            platform_admin=False,
            session_id="session-a",
            authorization_source="database",
        )

    def add_source(self, source_id: str, *, tenant: str = "tenant-a", name: str | None = None) -> None:
        self.session.add(ExternalSourceModel(
            id=source_id,
            tenant_id=tenant,
            source_key=f"key-{source_id}",
            source_type="google_drive",
            display_name=name,
            source_metadata={},
        ))

    def add_scope(self, source_id: str, folder_id: str, *, tenant: str = "tenant-a", name: str = "Assigned") -> None:
        self.session.add(ViewerFolderScopeModel(
            tenant_id=tenant,
            tenant_membership_id="membership-a",
            external_source_id=source_id,
            folder_external_id=folder_id,
            folder_name=name,
        ))

    def test_one_source_and_root_are_auto_selected(self) -> None:
        self.add_source("source-a", name="Marketing Drive")
        self.add_scope("source-a", "folder-a", name="Campaigns")
        self.session.commit()

        result = viewer_bootstrap("google-drive", self.session, self.principal())

        self.assertEqual(result["auto_selected_source_id"], "source-a")
        self.assertEqual(result["auto_selected_folder_id"], "folder-a")
        self.assertEqual(result["sources"][0]["folders"][0]["name"], "Campaigns")

    def test_one_source_with_multiple_roots_selects_only_source(self) -> None:
        self.add_source("source-a")
        self.add_scope("source-a", "folder-a")
        self.add_scope("source-a", "folder-b")
        self.session.commit()

        result = viewer_bootstrap("google-drive", self.session, self.principal())

        self.assertEqual(result["auto_selected_source_id"], "source-a")
        self.assertIsNone(result["auto_selected_folder_id"])
        self.assertEqual({folder["id"] for folder in result["sources"][0]["folders"]}, {"folder-a", "folder-b"})

    def test_multiple_sources_require_picker_and_keep_equal_folder_ids_isolated(self) -> None:
        self.add_source("source-a", name="A")
        self.add_source("source-b", name="B")
        self.add_scope("source-a", "same-folder")
        self.add_scope("source-b", "same-folder")
        self.add_source("source-other", tenant="tenant-b", name="Forbidden")
        self.add_scope("source-other", "hidden", tenant="tenant-b")
        self.session.commit()

        result = viewer_bootstrap("google-drive", self.session, self.principal())

        self.assertIsNone(result["auto_selected_source_id"])
        self.assertIsNone(result["auto_selected_folder_id"])
        self.assertEqual([source["external_source_id"] for source in result["sources"]], ["source-a", "source-b"])
        self.assertEqual(
            [source["folders"][0]["external_source_id"] for source in result["sources"]],
            ["source-a", "source-b"],
        )

    def test_no_scope_returns_structured_permission_error(self) -> None:
        self.add_source("source-a")
        self.session.commit()

        with self.assertRaises(HTTPException) as raised:
            viewer_bootstrap("google-drive", self.session, self.principal())

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail["code"], "viewer_folder_scope_required")

    def test_operator_is_not_changed_into_a_viewer_bootstrap_flow(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            viewer_bootstrap(
                "google-drive",
                self.session,
                self.principal(roles=frozenset({"viewer", "operator"})),
            )
        self.assertEqual(raised.exception.detail["code"], "viewer_bootstrap_not_applicable")


if __name__ == "__main__":
    unittest.main()
