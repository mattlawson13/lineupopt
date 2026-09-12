from app.ownership.model import PlayerOwnershipInput, compute_chalk_contrarian_scores, project_ownership


def make_players():
    """A realistically-sized pool (several players competing per position)
    so ownership mass spreads out rather than concentrating on one player
    and clipping at the configured ceiling — see ownership_settings.yaml's
    `max_ownership_pct`, which a too-small fixture can trivially saturate.
    """
    players = [
        PlayerOwnershipInput("qb1", "QB", 7500, 24.0, 26.0, -6.0, 22.0),
        PlayerOwnershipInput("qb2", "QB", 5200, 16.0, 19.0, 6.0, 14.0),
        PlayerOwnershipInput("rb1", "RB", 8000, 20.0, 26.0, -6.0, 18.0),
        PlayerOwnershipInput("rb2", "RB", 4200, 9.0, 19.0, 6.0, 8.0),
        PlayerOwnershipInput("wr1", "WR", 7200, 18.0, 26.0, -6.0, 16.0),
        PlayerOwnershipInput("wr2", "WR", 3600, 7.0, 19.0, 6.0, 6.0),
    ]
    # Filler players spread across each position so the softmax
    # normalization has realistic competition for roster-slot share.
    for i in range(10):
        players.append(PlayerOwnershipInput(f"qb_filler{i}", "QB", 5000 + i * 100, 14.0 - i * 0.3, 20.0, 0.0, 12.0))
        players.append(PlayerOwnershipInput(f"rb_filler{i}", "RB", 4500 + i * 150, 10.0 - i * 0.3, 21.0, 0.0, 9.0))
        players.append(PlayerOwnershipInput(f"wr_filler{i}", "WR", 4200 + i * 150, 9.0 - i * 0.3, 21.0, 0.0, 8.0))
    return players


def test_ownership_percentages_are_bounded():
    results = project_ownership(make_players())
    for r in results:
        assert 0 < r.projected_ownership_pct <= 100


def test_better_value_gets_more_ownership_within_position():
    results = project_ownership(make_players())
    by_id = {r.player_id: r for r in results}
    # rb1 is the higher-projected, higher-salary play; rb2 is a cheap punt
    # with a much worse points-per-dollar value — expect rb1 more owned.
    assert by_id["rb1"].projected_ownership_pct > by_id["rb2"].projected_ownership_pct


def test_chalk_and_contrarian_scores_are_complementary():
    results = project_ownership(make_players())
    scores = compute_chalk_contrarian_scores(results)
    for pid, s in scores.items():
        assert round(s["chalk_score"] + s["contrarian_score"], 1) == 100.0


def test_most_owned_player_has_highest_chalk_score():
    results = project_ownership(make_players())
    scores = compute_chalk_contrarian_scores(results)
    max_ownership = max(r.projected_ownership_pct for r in results)
    # Two clear top plays can legitimately tie at the configured ownership
    # ceiling (ownership_settings.yaml's max_ownership_pct) — assert the
    # highest chalk score belongs to *a* player at that ceiling, not that
    # there's a single unambiguous winner.
    most_owned_ids = {r.player_id for r in results if r.projected_ownership_pct == max_ownership}
    highest_chalk_score = max(s["chalk_score"] for s in scores.values())
    highest_chalk_ids = {pid for pid, s in scores.items() if s["chalk_score"] == highest_chalk_score}
    assert highest_chalk_ids & most_owned_ids


def test_injury_penalizes_ownership():
    # Remove the fixture's own wr1 so these two are the clear top WR play
    # (isolating the injury-status effect) without both saturating the
    # ownership ceiling — see make_players()'s docstring on clipping.
    players = [p for p in make_players() if p.player_id != "wr1"]
    healthy_copy = PlayerOwnershipInput("wr1_healthy", "WR", 7200, 18.0, 26.0, -6.0, 16.0, injury_status="healthy")
    questionable_copy = PlayerOwnershipInput("wr1_quest", "WR", 7200, 18.0, 26.0, -6.0, 16.0, injury_status="questionable")
    results = project_ownership(players + [healthy_copy, questionable_copy])
    by_id = {r.player_id: r for r in results}
    assert by_id["wr1_healthy"].projected_ownership_pct > by_id["wr1_quest"].projected_ownership_pct
