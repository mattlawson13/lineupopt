"""Single source of truth for "is this player even a real, playable
option this week" — every existing discount (depth chart multiplier,
injury availability, market projection scaling) already pushes a truly
unplayable player's ensemble projection toward zero, but a heavily
discounted player was still nominally ELIGIBLE everywhere this got
checked independently, and those checks drifted: the main build pipeline
(ingestion/slate_builder.py) and the ownership model (ownership/model.py)
each grew their own copy of this floor, and the manual/"Generate More"/
late-swap pipeline (api/routes_lineups.py) never got one at all —
confirmed live: a late-swap on a real Showdown slate (where the single
game had already kicked off) produced a lineup of six $200-salary,
0-point bench players once every real option got excluded, precisely
because that code path had nothing to keep them out of consideration in
the first place.

One shared constant/helper here, imported everywhere a player pool gets
built for either the optimizer or the ownership model, so this can never
drift out of sync between code paths again.
"""
from __future__ import annotations

UNPLAYABLE_PROJECTION_FLOOR = 1.0


def is_playable(ensemble_projection: float) -> bool:
    return ensemble_projection >= UNPLAYABLE_PROJECTION_FLOOR
