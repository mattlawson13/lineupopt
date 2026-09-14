"""Builds a CSV in the shape DraftKings' own bulk-upload contest-entry
tool expects, so generated lineups can go straight from this app into a
real DK contest without hand-retyping every roster.

Column headers are each roster slot name repeated `count` times, in the
same order DK's own config lists them (e.g. Classic:
QB,RB,RB,WR,WR,WR,TE,FLEX,DST; Showdown: CPT,FLEX,FLEX,FLEX,FLEX,FLEX) —
this matches the header row DK's own "Download Player List" /
contest-entry CSV template uses. Each player cell is formatted
"Display Name (dk_player_id)", DK's own required format for identifying
which specific player a cell refers to (name alone is ambiguous — two
players can share a name, and DK's upload parser keys off the numeric ID
in parens).

Deliberately does NOT include DK's own Entry ID / Contest ID / Contest
Name columns — those only exist once you've actually entered a contest
(DK generates them per real entry you hold and won't hand them out any
other way), so this app has no way to know them. Pair this file with
DK's own downloaded contest-entry template: open both, copy this file's
player columns into the matching rows of DK's template (which already
has your real Entry ID/Contest ID pre-filled), then upload that.
"""
from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lineup import Lineup
from app.models.slate import DraftKingsPlayer, Slate
from app.optimization.dk_rules import get_contest_rules


def build_dk_bulk_upload_csv(db: Session, slate: Slate, lineups: list[Lineup]) -> str:
    rules = get_contest_rules(slate.sport, slate.contest_type)

    header: list[str] = []
    for slot in rules.slots:
        header.extend([slot.name] * slot.count)

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate.id)).scalars().all()
    dk_by_player_id = {r.player_id: r for r in dk_rows if r.player_id}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)

    for lineup in lineups:
        # Group by slot name first — a slot like "RB" or "WR" is reused
        # across multiple header columns (count > 1), so each occupant
        # needs to land in a distinct column, not overwrite the same one.
        by_slot: dict[str, list[str]] = {}
        for lp in lineup.players:
            dk_row = dk_by_player_id.get(lp.player_id)
            cell = f"{dk_row.display_name} ({dk_row.dk_player_id})" if dk_row else ""
            by_slot.setdefault(lp.roster_slot, []).append(cell)

        row = []
        used_index: dict[str, int] = {}
        for slot_name in header:
            i = used_index.get(slot_name, 0)
            values = by_slot.get(slot_name, [])
            row.append(values[i] if i < len(values) else "")
            used_index[slot_name] = i + 1
        writer.writerow(row)

    return buf.getvalue()
