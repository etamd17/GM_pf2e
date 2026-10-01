"""Pure account-mode permissions; legacy sessions keep their existing routes."""

from core.request_context import CampaignContext, PrincipalKind
from .types import Capabilities, CharacterRecord


def capabilities_for(context: CampaignContext,
                     character: CharacterRecord | None) -> Capabilities:
    principal = context.principal
    if (principal.kind is not PrincipalKind.USER or not context.is_live
            or context.system not in {'pf2e', 'cosmere'}):
        return Capabilities()
    create = context.is_member
    gm = context.is_gm or principal.is_admin
    if character is None:
        return Capabilities(create=create, override=gm)
    if (character.campaign_id != context.campaign_id
            or character.system != context.system):
        return Capabilities(create=create)
    if not (context.is_member or gm):
        return Capabilities()
    owner = principal.user_id == character.owner_id
    editor = principal.user_id in character.editor_ids
    viewer = principal.user_id in character.viewer_ids
    edit = gm or owner or editor
    return Capabilities(
        create=create,
        view=edit or (character.system == 'pf2e' and viewer),
        edit=edit,
        delete=gm or (character.system == 'cosmere' and owner),
        manage_ownership=gm,
        override=gm,
        owner_private=gm or owner,
    )
