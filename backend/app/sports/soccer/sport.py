"""Soccer sport module — SCAFFOLD, not production-ready.

What's real: the `Sport` interface registration, DK Soccer scoring/roster
config (config/dk_scoring_soccer.yaml, config/dk_roster_rules_soccer.yaml),
and a first pass at soccer's correlation relationships (clean-sheet
correlation between a team's GK/defenders, attacking correlation between
midfielders/forwards, negative correlation vs the opposing GK) — these are
genuinely soccer-shaped, not NFL logic relabeled.

What's NOT implemented yet (raises NotImplementedError rather than
silently running NFL math on soccer players, per spec section 25's "Do not
force NFL assumptions onto Soccer"):
  - A soccer projection model. Soccer needs its own component model over
    shots/shots-on-target/key passes/chances-created/xG-style inputs and
    starting-XI probability — nothing like `projections/nfl/*`'s
    pass/rush/target rate math applies.
  - Soccer game-script simulation (`game_script_names`) — score-state
    dynamics in soccer (chasing a goal, sitting on a lead, red cards) are
    structurally different from NFL's spread/total-driven scripts and need
    their own scenario model.
  - `PlayerProjectionContext` itself (imported from `projections/nfl/common`)
    is NFL-shaped (pass/rush/weather fields) — a real soccer implementation
    should define its own context dataclass (shots, xG, starting-XI prob,
    set-piece role) rather than shoehorning soccer data into NFL's fields.

Phase 4 in the project roadmap (see README) is standing this up for real.
"""
from __future__ import annotations

from app.projections.nfl.common import ComponentProjection, PlayerProjectionContext
from app.sports.base import Sport


class SoccerSport(Sport):
    key = "soccer"
    positions = ["GK", "D", "M", "F"]

    def scoring_config_name(self) -> str:
        return "dk_scoring_soccer.yaml"

    def roster_config_name(self, contest_type: str) -> str:
        return "dk_roster_rules_soccer.yaml"

    def project_player(self, ctx: PlayerProjectionContext) -> ComponentProjection:
        raise NotImplementedError(
            "Soccer projection model not yet implemented — see this module's docstring. "
            "NFL's component model must not be reused here (different stat structure entirely)."
        )

    def classify_correlation_relationship(self, position_a: str, position_b: str, same_team: bool) -> str | None:
        pair = tuple(sorted([position_a, position_b]))
        if same_team:
            if pair == ("D", "GK"):
                return "gk_own_defender_clean_sheet"
            if pair == ("F", "M"):
                return "midfielder_own_forward_attack"
            if pair == ("F", "F"):
                return "forward_own_forward_target_competition"  # typically slightly negative — shared chances
            return None
        # opponents
        if pair == ("F", "GK"):
            return "forward_opp_goalkeeper"  # negative — a strong opposing GK suppresses forward output
        if pair == ("D", "F"):
            return "defender_opp_forward_clean_sheet_risk"
        return None

    def game_script_names(self) -> list[str]:
        raise NotImplementedError(
            "Soccer game-script simulation not yet implemented — score-state dynamics "
            "(chasing/sitting on a lead, red cards) need a soccer-specific scenario model."
        )
