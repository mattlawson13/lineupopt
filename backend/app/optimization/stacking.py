"""Classifies a generated lineup's stack type and produces a human-
readable description — spec section 16. Classification happens after
optimization (it's descriptive, not prescriptive) but `forced_team_min_counts`
in optimizer.py lets the portfolio builder *require* a given stack shape
up front when a contest mode calls for it.
"""
from __future__ import annotations

import dataclasses

from app.models.enums import StackType
from app.optimization.optimizer import LineupPlayerAssignment, OptimizerPlayer


@dataclasses.dataclass
class StackClassification:
    stack_type: StackType | None
    description: str


def classify_stack(
    assignments: list[LineupPlayerAssignment],
    players_by_id: dict[str, OptimizerPlayer],
) -> StackClassification:
    lineup_players = [players_by_id[a.player_id] for a in assignments]
    qbs = [p for p in lineup_players if p.position == "QB"]
    if not qbs:
        return StackClassification(None, "No-QB lineup (game-script / value build, not stack-classified)")

    qb = qbs[0]
    qb_game = qb.game_id
    opponent_players = [p for p in lineup_players if p.game_id == qb_game and p.team != qb.team]

    own_catchers = [p for p in lineup_players if p.team == qb.team and p.position in ("WR", "TE")]
    opp_catchers = [p for p in opponent_players if p.position in ("WR", "TE")]
    opp_rb = [p for p in opponent_players if p.position == "RB"]

    own_n, opp_n = len(own_catchers), len(opp_catchers)

    if own_n >= 2 and opp_n >= 1:
        stack_type = StackType.GAME_STACK
        desc = (
            f"Game stack: {qb.player_id} + {own_n} {qb.team} pass-catchers "
            f"with a {opp_n}-player bring-back from {opponent_players[0].team if opponent_players else 'opponent'} "
            "in the same game."
        )
    elif own_n >= 1 and opp_rb and opp_n == 0:
        stack_type = StackType.RUN_BACK
        desc = f"Run-back: {qb.team} QB/pass-catcher stack paired with the opposing RB from the same game."
    elif own_n >= 1 and opp_n >= 1:
        stack_type = StackType.BRING_BACK
        desc = f"Bring-back: {qb.team} QB + pass-catcher, plus a {opponent_players[0].team} pass-catcher for game-total correlation."
    elif own_n >= 2:
        stack_type = StackType.DOUBLE_STACK
        desc = f"Double stack: {qb.team} QB with {own_n} of his own pass-catchers."
    elif own_n == 1:
        stack_type = StackType.STANDARD
        desc = f"Standard stack: {qb.team} QB + 1 pass-catcher."
    else:
        stack_type = StackType.NAKED_QB
        desc = f"Naked QB: {qb.team} QB with no correlated pass-catcher in the lineup."

    return StackClassification(stack_type, desc)
