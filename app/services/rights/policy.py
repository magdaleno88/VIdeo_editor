from typing import Any

from app.schemas.domain import (
    RightsInfo,
    RightsStatus,
    SourceLicensePreset,
    SourceLicensePresetRead,
)

_LICENSE_PRESETS = {
    SourceLicensePreset.CC0: {
        "license_name": "CC0 1.0 Universal",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "commercial_use_allowed": True,
        "derivative_works_allowed": True,
        "attribution_required": False,
        "share_alike_required": False,
    },
    SourceLicensePreset.PUBLIC_DOMAIN: {
        "license_name": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "commercial_use_allowed": True,
        "derivative_works_allowed": True,
        "attribution_required": False,
        "share_alike_required": False,
    },
    SourceLicensePreset.CC_BY: {
        "license_name": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "commercial_use_allowed": True,
        "derivative_works_allowed": True,
        "attribution_required": True,
        "share_alike_required": False,
    },
    SourceLicensePreset.CC_BY_SA: {
        "license_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "commercial_use_allowed": True,
        "derivative_works_allowed": True,
        "attribution_required": True,
        "share_alike_required": True,
    },
    SourceLicensePreset.CUSTOM: {
        "license_name": None,
        "license_url": None,
        "commercial_use_allowed": None,
        "derivative_works_allowed": None,
        "attribution_required": None,
        "share_alike_required": None,
    },
}


def license_preset(preset: SourceLicensePreset) -> SourceLicensePresetRead:
    return SourceLicensePresetRead(preset=preset, **_LICENSE_PRESETS[preset])


def license_presets() -> list[SourceLicensePresetRead]:
    return [license_preset(preset) for preset in SourceLicensePreset]


def commercial_publication_blockers(rights: Any) -> list[str]:
    modification_allowed = getattr(
        rights, "derivative_works_allowed", getattr(rights, "modification_allowed", None)
    )
    evidence = getattr(rights, "evidence_reference", getattr(rights, "evidence_url", None))
    reviewer = getattr(rights, "reviewed_by", getattr(rights, "verified_by", None))
    verified_at = getattr(rights, "verified_at", getattr(rights, "verification_date", None))
    blockers = []
    if rights.rights_status != RightsStatus.VERIFIED:
        blockers.append("Rights must be VERIFIED by a human reviewer")
    if rights.commercial_use_allowed is not True:
        blockers.append("Commercial use permission is missing")
    if modification_allowed is not True:
        blockers.append("Derivative work permission is missing")
    if not getattr(rights, "license_name", None) or not evidence:
        blockers.append("License and evidence are required")
    if not reviewer or not verified_at:
        blockers.append("Reviewer and verification date are required")
    if getattr(rights, "attribution_required", None) is None:
        blockers.append("Attribution requirements are unknown")
    elif rights.attribution_required and not getattr(rights, "attribution_text", None):
        blockers.append("Required attribution text is missing")
    return blockers


def is_cleared_for_commercial_publication(rights: Any) -> bool:
    return not commercial_publication_blockers(rights)


def publication_clearance_label(rights: Any) -> str:
    if is_cleared_for_commercial_publication(rights):
        return "CLEARED FOR COMMERCIAL PRODUCTION"
    if rights.rights_status == RightsStatus.RESTRICTED:
        return "RESTRICTED — NOT CLEARED FOR PUBLICATION"
    return "NOT CLEARED FOR PUBLICATION"


def approval_blockers(rights: RightsInfo) -> list[str]:
    blockers = commercial_publication_blockers(rights)
    if not rights.license_url:
        blockers.append("License URL is required")
    return blockers


def local_processing_rights_blockers(candidate: Any) -> list[str]:
    if str(candidate.provider) == "long_form":
        return []
    return approval_blockers(RightsInfo.model_validate(candidate.rights))
