from __future__ import annotations

from app.projections.nfl import dst, kicker, qb, rb, wr_te
from app.projections.nfl.common import ComponentProjection, PlayerProjectionContext

_DISPATCH = {
    "QB": qb.project,
    "RB": rb.project,
    "WR": wr_te.project,
    "TE": wr_te.project,
    "K": kicker.project,
    "DST": dst.project,
}


def project_player(ctx: PlayerProjectionContext) -> ComponentProjection:
    fn = _DISPATCH.get(ctx.position)
    if fn is None:
        raise ValueError(f"No NFL projection model for position {ctx.position!r}")
    return fn(ctx)
