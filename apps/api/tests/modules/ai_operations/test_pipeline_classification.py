from app.modules.ai_operations.pipeline import PipelineOperationsRepository


def test_invalid_image_signatures_are_skipped_as_unsupported():
    assert (
        PipelineOperationsRepository._skip_category(
            "InvalidPipelineContent",
            "declared image content has an invalid ISO-BMFF image signature",
        )
        == "unsupported"
    )
    assert (
        PipelineOperationsRepository._skip_category(
            "InvalidPipelineContent",
            "unsupported file signature",
        )
        == "unsupported"
    )


def test_provider_download_rejection_remains_actionable():
    assert (
        PipelineOperationsRepository._skip_category(
            "InvalidPipelineContent",
            "source provider rejected the download request",
        )
        is None
    )


def test_oversized_content_keeps_oversized_category():
    assert (
        PipelineOperationsRepository._skip_category(
            "InvalidPipelineContent",
            "source exceeded byte limit",
        )
        == "oversized"
    )
