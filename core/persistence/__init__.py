"""Additive transactional persistence foundation.

Importing this package defines models and helpers only. It does not read
``DATABASE_URL``, construct an engine, run migrations, or alter the JSON
runtime authority.
"""

from .database import Database, normalize_database_url
from .models import (
    AuditEvent,
    Base,
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    Draft,
    Invitation,
    InviteRedemption,
    MigrationRun,
    User,
)
from .repositories import (
    CampaignRepository,
    CharacterRepository,
    InvitationRepository,
    MigrationRunRepository,
    UserRepository,
    normalize_invite_code,
    normalize_username,
)
from .services import (
    CharacterAlreadyClaimedError,
    CharacterClaimResult,
    InviteRedemptionResult,
    InviteUnavailableError,
    LastGameMasterError,
    MembershipRequiredError,
    RecordNotFoundError,
    TransactionalStore,
    TransactionalStoreError,
)

# Domain-friendly names matching the physical tables and migration language.
Invite = Invitation
CharacterDraft = Draft

__all__ = [
    "AuditEvent",
    "Base",
    "Campaign",
    "CampaignMembership",
    "CampaignRepository",
    "Character",
    "CharacterAlreadyClaimedError",
    "CharacterAssignment",
    "CharacterClaimResult",
    "CharacterDraft",
    "CharacterRepository",
    "Database",
    "Draft",
    "Invitation",
    "InvitationRepository",
    "Invite",
    "InviteRedemption",
    "InviteRedemptionResult",
    "InviteUnavailableError",
    "LastGameMasterError",
    "MembershipRequiredError",
    "MigrationRun",
    "MigrationRunRepository",
    "RecordNotFoundError",
    "TransactionalStore",
    "TransactionalStoreError",
    "User",
    "UserRepository",
    "normalize_database_url",
    "normalize_invite_code",
    "normalize_username",
]
