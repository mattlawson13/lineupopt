"""Loads YAML configuration from this directory.

Every tunable number in the system (scoring rules, roster rules, ensemble
weights, simulation/optimization/ownership/correlation settings) should be
read through this module rather than hardcoded. Results are cached per
process; call `reload()` to force a re-read (e.g. after an admin edits a
config file).
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).parent


def _load_yaml(filename: str) -> Any:
    path = CONFIG_DIR / filename
    with path.open("r") as f:
        docs = list(yaml.safe_load_all(f))
    return docs[0] if len(docs) == 1 else docs


def _load_yaml_multidoc(filename: str) -> list[dict]:
    path = CONFIG_DIR / filename
    with path.open("r") as f:
        return [d for d in yaml.safe_load_all(f) if d]


@functools.lru_cache
def get_sports_config() -> dict:
    return _load_yaml("sports.yaml")


@functools.lru_cache
def get_dk_scoring(sport: str = "nfl") -> dict:
    sports = get_sports_config()["sports"]
    if sport not in sports:
        raise ValueError(f"Unknown sport: {sport}")
    return _load_yaml(sports[sport]["scoring_config"])


@functools.lru_cache
def get_dk_roster_rules(sport: str = "nfl", contest_type: str = "classic") -> dict:
    sports = get_sports_config()["sports"]
    docs = _load_yaml_multidoc(sports[sport]["roster_config"])
    for doc in docs:
        if doc.get("contest_type") == contest_type:
            return doc
    raise ValueError(f"No roster rules for sport={sport} contest_type={contest_type}")


@functools.lru_cache
def get_projection_weights() -> dict:
    return _load_yaml("projection_weights.yaml")


@functools.lru_cache
def get_simulation_settings() -> dict:
    return _load_yaml("simulation_settings.yaml")


@functools.lru_cache
def get_optimization_settings() -> dict:
    return _load_yaml("optimization_settings.yaml")


@functools.lru_cache
def get_ownership_settings() -> dict:
    return _load_yaml("ownership_settings.yaml")


@functools.lru_cache
def get_correlation_settings() -> dict:
    return _load_yaml("correlation_settings.yaml")


def reload() -> None:
    """Clear all cached config so the next getter re-reads from disk."""
    for fn in (
        get_sports_config,
        get_dk_scoring,
        get_dk_roster_rules,
        get_projection_weights,
        get_simulation_settings,
        get_optimization_settings,
        get_ownership_settings,
        get_correlation_settings,
    ):
        fn.cache_clear()
