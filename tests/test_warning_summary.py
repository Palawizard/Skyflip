from skyflip.warning_summary import compact_warnings


def test_compact_warnings_groups_skycofl_failures():
    warnings = [
        "SkyCofl analysis unavailable for A: HTTP 429 for https://example.test/a",
        "SkyCofl sold auctions unavailable for B: HTTP 429 for https://example.test/b",
        "SkyCofl active/bin unavailable for PET;0: 400 Client Error: Bad Request for url",
        "SkyCofl active overview unavailable for PET;0: 400 Client Error: Bad Request for url",
    ]

    assert compact_warnings(warnings) == [
        "SkyCofl rate limited market checks: 2 checks; examples: A, B.",
        "SkyCofl rejected unsupported market checks: 2 checks; examples: PET;0.",
    ]


def test_compact_warnings_deduplicates_regular_messages():
    warnings = ["Craft section failed: timeout", "Craft section failed: timeout"]

    assert compact_warnings(warnings) == ["Craft section failed: timeout"]
