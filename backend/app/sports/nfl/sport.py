from __future__ import annotations

from app.correlations.engine import CorrelationPlayer, classify_relationship
from app.models.enums import GameScript
from app.projections.nfl.common import ComponentProjection, PlayerProjectionContext
from app.projections.nfl.model import project_player
from app.sports.base import Sport


class NFLSport(Sport):
    key = "nfl"
    positions = ["QB", "RB", "WR", "TE", "K", "DST"]

    def scoring_config_name(self) -> str:
        return "dk_scoring_nfl.yaml"

    def roster_config_name(self, contest_type: str) -> str:
        return "dk_roster_rules_nfl.yaml"

    def project_player(self, ctx: PlayerProjectionContext) -> ComponentProjection:
        return project_player(ctx)

    def classify_correlation_relationship(self, position_a: str, position_b: str, same_team: bool) -> str | None:
        a = CorrelationPlayer(player_id="a", position=position_a, team="X", opponent="Y")
        b = CorrelationPlayer(player_id="b", position=position_b, team="X" if same_team else "Y", opponent="Y" if same_team else "X")
        return classify_relationship(a, b)

    def game_script_names(self) -> list[str]:
        return [s.value for s in GameScript]
