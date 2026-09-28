from app.schemas.domain import RightsInfo, RightsStatus


def approval_blockers(rights: RightsInfo) -> list[str]:
    blockers = []
    if rights.rights_status != RightsStatus.VERIFIED:
        blockers.append("Rights must be VERIFIED by a human reviewer")
    if rights.commercial_use_allowed is not True:
        blockers.append("Commercial use permission is missing")
    if rights.modification_allowed is not True:
        blockers.append("Modification permission is missing")
    if not (rights.license_name and rights.license_url and rights.evidence_url):
        blockers.append("License and evidence references are required")
    if not (rights.verification_date and rights.verified_by):
        blockers.append("Verification date and reviewer are required")
    if rights.attribution_required is None:
        blockers.append("Attribution requirements are unknown")
    elif rights.attribution_required and not rights.attribution_text:
        blockers.append("Required attribution text is missing")
    return blockers
