"""Public Cosmere builder facade; a server-resolved profile owns mechanics.

Legacy helper imports remain available for Stormlight callers. New mechanical
profiles implement their build engine behind ``create_build``.
"""
from .stormlight_build import (  # noqa: F401 -- historical public helper API
    ATTR_KEYS, CREATION_ATTR_POINTS, CREATION_ATTR_MAX, ATTR_HARD_CAP,
    ATTR_INCREASE_LEVELS, TIER_START_LEVELS, MAX_SKILL_RANK_BY_TIER,
    CREATION_FREE_SKILL_RANKS, PATH_SKILL_RANK, attribute_points, max_skill_rank,
    free_skill_ranks, total_skill_ranks, base_talents, ancestry_bonus_talents,
    total_talents, expertises_total, movement_rate, senses_range,
    lifting_capacity, carrying_capacity, health_gain_at, level_grants,
    tier_of, cosmere_max_health,
)
from .rules.profiles import resolve_profile, normalize_theme
from .rules.schema import to_dict


class CosmereBuild:
    """Compatibility facade over the build engine chosen by exact package pins."""

    def __init__(self, data=None, homebrew=None, *, trusted_goal_entitlements=None):
        document = dict(data or {})
        object.__setattr__(self, 'profile', resolve_profile(document, homebrew=homebrew))
        object.__setattr__(self, 'rules_binding', self.profile.binding)
        object.__setattr__(self, 'cosmere_theme', normalize_theme(document))
        kwargs = {}
        if trusted_goal_entitlements is not None:
            kwargs['trusted_goal_entitlements'] = tuple(trusted_goal_entitlements)
        object.__setattr__(self, '_implementation',
                          self.profile.create_build(document, **kwargs))

    def __getattr__(self, name):
        return getattr(self._implementation, name)

    def __setattr__(self, name, value):
        if name in {'profile', 'rules_binding'}:
            raise AttributeError('Rules profile and binding are immutable; construct a new build.')
        if name == 'cosmere_theme':
            object.__setattr__(self, name, normalize_theme({'cosmere_theme': value}))
        else:
            setattr(self._implementation, name, value)

    def validate(self):
        return list(self.profile.validate_build(self._implementation))

    @property
    def is_valid(self):
        return not self.validate()

    def to_dict(self):
        return {**self._implementation.to_dict(), 'rules_binding': to_dict(self.rules_binding),
                'cosmere_theme': self.cosmere_theme}

    def to_actor_doc(self):
        document = dict(self.profile.project_actor(self._implementation))
        document['system'] = {**document['system'], 'rules_binding': to_dict(self.rules_binding),
                              'cosmere_theme': self.cosmere_theme, 'cosmere_build': self.to_dict()}
        return document

    @classmethod
    def from_actor_doc(cls, doc):
        system = (doc or {}).get('system', {}) if isinstance(doc, dict) else {}
        stashed = system.get('cosmere_build')
        build = dict(stashed) if isinstance(stashed, dict) else {}
        for key in ('rules_binding', 'cosmere_theme'):
            if key in system:
                build[key] = system[key]
        return cls(build)
