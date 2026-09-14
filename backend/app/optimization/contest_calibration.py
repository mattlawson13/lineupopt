"""Turns a *specific* contest's real field size into continuously-scaled
optimizer objective weights, instead of the fixed 4-bucket contest_modes
in config/optimization_settings.yaml — a 47,562-max-entry contest and a
500,000-max-entry contest both used to get the identical "large_field_gpp"
weights, with no way for the objective to react to how big THIS
particular contest's field actually is (user report: "if I'm doing large
GPP and just want first place, will the button accommodate that?" — not
precisely, until now).

Honest scope: this scales purely on field size (log-interpolated between
the single_entry and large_field_gpp weight buckets already in the
config) — a well-established DFS principle that bigger fields need more
ceiling/differentiation for a realistic shot at 1st, and smaller/softer
fields reward a more balanced build. It does NOT model a contest's actual
payout curve (e.g. "$50K to 1st" vs a flatter curve): DraftKings' public
lobby listing (data_sources/draftkings.py's get_nfl_contests) only
exposes a contest's total prize pool, not a rank-by-rank payout table —
no verified DK endpoint for that exists in this codebase, and this
module doesn't pretend otherwise by fabricating one.
"""
from __future__ import annotations

import math

from app.config.loader import get_optimization_settings

_SMALL_FIELD_REF = 1        # single-entry-style contest -> single_entry weights
_LARGE_FIELD_REF = 200_000  # a mega-GPP-sized field -> large_field_gpp weights


def contest_calibrated_weights(max_entries: int) -> dict:
    """Continuously interpolates (log-scaled on field size) between the
    single_entry and large_field_gpp objective_weights buckets, so a
    47,562-entry contest and a 500,000-entry contest get measurably
    different weights instead of both collapsing into one "large" bucket.
    """
    cfg = get_optimization_settings()["contest_modes"]
    small = cfg["single_entry"]["objective_weights"]
    large = cfg["large_field_gpp"]["objective_weights"]

    lo, hi = math.log10(_SMALL_FIELD_REF), math.log10(_LARGE_FIELD_REF)
    x = math.log10(max(max_entries, 1))
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))

    keys = set(small) | set(large)
    return {k: round((1 - t) * small.get(k, 0.0) + t * large.get(k, 0.0), 4) for k in keys}
