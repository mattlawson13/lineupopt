"""Assembles the full player-facing "Why?" transparency panel (spec
section 34) by merging the component model's additive breakdown with the
ensemble blend that sits on top of it.
"""
from __future__ import annotations

from app.projections.ensemble import EnsembleResult
from app.projections.nfl.common import ComponentProjection


def build_why_panel(component: ComponentProjection, ensemble: EnsembleResult) -> dict:
    panel = component.why_panel()
    panel["ensemble"] = {
        "final_projection": ensemble.ensemble_projection,
        "floor": ensemble.floor,
        "median": ensemble.median,
        "ceiling": ensemble.ceiling,
        "std_dev": ensemble.std_dev,
        "weights_used": ensemble.weights_used,
    }
    return panel
