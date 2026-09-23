import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


class CreativePipelineGptSkillsMigrationTest(unittest.TestCase):
    def test_upgrade_seeds_versioned_skills_and_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0090_creative_pipeline_gpt_skills")

            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
            self.assertTrue(
                {
                    "creative_pipeline_skills",
                    "creative_pipeline_skill_versions",
                    "creative_pipeline_skill_bindings",
                    "creative_pipeline_skill_executions",
                }.issubset(table_names)
            )

            version_columns = {
                item["name"]
                for item in inspector.get_columns("creative_pipeline_skill_versions")
            }
            self.assertTrue(
                {
                    "owner_tenant_id",
                    "instructions",
                    "input_schema_json",
                    "output_schema_json",
                    "knowledge_refs_json",
                    "preferred_model",
                }.issubset(version_columns)
            )

            skill_indexes = {
                item["name"] for item in inspector.get_indexes("creative_pipeline_skills")
            }
            self.assertTrue(
                {
                    "uq_cp_skills_system_key",
                    "uq_cp_skills_tenant_key",
                }.issubset(skill_indexes)
            )
            version_indexes = {
                item["name"]
                for item in inspector.get_indexes("creative_pipeline_skill_versions")
            }
            self.assertTrue(
                {
                    "uq_cp_skill_versions_system_number",
                    "uq_cp_skill_versions_tenant_number",
                }.issubset(version_indexes)
            )

            execution_columns = {
                item["name"]
                for item in inspector.get_columns(
                    "creative_pipeline_skill_executions"
                )
            }
            self.assertTrue(
                {
                    "skill_version_id",
                    "binding_scope",
                    "variant_key",
                    "input_artifact_ids_json",
                    "output_artifact_ids_json",
                    "knowledge_snapshot_id",
                    "prompt_sha256",
                    "usage_json",
                }.issubset(execution_columns)
            )

            with engine.connect() as connection:
                skills = connection.execute(
                    text(
                        "SELECT skill_key, node_type FROM creative_pipeline_skills "
                        "ORDER BY skill_key"
                    )
                ).all()
                versions = connection.execute(
                    text(
                        "SELECT s.skill_key, v.version, v.status "
                        "FROM creative_pipeline_skill_versions v "
                        "JOIN creative_pipeline_skills s ON s.id = v.skill_id "
                        "ORDER BY s.skill_key"
                    )
                ).all()
            self.assertEqual(
                skills,
                [
                    ("creative-idea-story", "idea_story"),
                    ("creative-video-prompt", "prompt"),
                ],
            )
            self.assertEqual(
                versions,
                [
                    ("creative-idea-story", 1, "published"),
                    ("creative-video-prompt", 1, "published"),
                ],
            )
            engine.dispose()

            command.downgrade(config, "0089_r2_video_playback_derivative")
            engine = create_engine(f"sqlite:///{path}")
            table_names = set(inspect(engine).get_table_names())
            self.assertNotIn("creative_pipeline_skills", table_names)
            self.assertNotIn("creative_pipeline_skill_versions", table_names)
            self.assertNotIn("creative_pipeline_skill_bindings", table_names)
            self.assertNotIn("creative_pipeline_skill_executions", table_names)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()