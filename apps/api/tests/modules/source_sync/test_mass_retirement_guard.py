from app.modules.source_sync.repository import SourceSyncRepository


def test_blocks_mass_retirement_after_provider_identity_casing_change():
    assert SourceSyncRepository.mass_retirement_is_unsafe(
        active_count=137000, missing_count=68656
    )


def test_allows_small_incremental_source_deletions():
    assert not SourceSyncRepository.mass_retirement_is_unsafe(
        active_count=10000, missing_count=45
    )
    assert not SourceSyncRepository.mass_retirement_is_unsafe(
        active_count=900, missing_count=800
    )
    assert not SourceSyncRepository.mass_retirement_is_unsafe(
        active_count=5000, missing_count=999
    )
