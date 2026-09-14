"""Builds a CSV in the shape DraftKings' own bulk-upload contest-entry
tool expects, so generated lineups can go straight from this app into a
real DK contest without hand-retyping every roster.

Column headers are each roster slot name repeated `count` times, in the
same order DK's own config lists them (e.g. Classic:
QB,RB,RB,WR,WR,WR,TE,FLEX,DST; Showdown: CPT,FLEX,FLEX,FLEX,FLEX,FLEX) —
this matches the header row DK's own contest-entry CSV template uses.
Each player cell is formatted "Display Name (dk_draftable_id)" — DK's
required format for identifying which specific player a cell refers to.

Critically, the ID in parentheses must be DK's per-slate "draftableId",
NOT the stable cross-season "playerDkId" this app uses internally for
its own player matching (see data_sources/draftkings.py) — confirmed
live against a real DK-downloaded upload template (2026-09-14): the
numbers DK expects (e.g. 44105012) are a completely different, much
larger namespace than playerDkId (e.g. 787661), and Showdown assigns a
genuinely DIFFERENT draftableId to the same player's CPT slot vs FLEX
slot. Using the wrong one gets a row silently rejected by DK's own
upload validator — so this raises MissingDraftableIdError rather than
emit a CSV that looks right but would fail at DK, for any player whose
draftableId isn't on file (e.g. a slate captured before this field
existed) instead of silently falling back to the wrong ID.

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


class MissingDraftableIdError(RuntimeError):
    pass


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

    missing: list[str] = []
    for lineup in lineups:
        # Group by slot name first — a slot like "RB" or "WR" is reused
        # across multiple header columns (count > 1), so each occupant
        # needs to land in a distinct column, not overwrite the same one.
        by_slot: dict[str, list[str]] = {}
        for lp in lineup.players:
            dk_row = dk_by_player_id.get(lp.player_id)
            draftable_id = (
                (dk_row.dk_captain_draftable_id or dk_row.dk_draftable_id) if dk_row and lp.roster_slot == "CPT"
                else dk_row.dk_draftable_id if dk_row
                else None
            )
            if dk_row and not draftable_id:
                missing.append(dk_row.display_name)
                continue
            cell = f"{dk_row.display_name} ({draftable_id})" if dk_row else ""
            by_slot.setdefault(lp.roster_slot, []).append(cell)

        row = []
        used_index: dict[str, int] = {}
        for slot_name in header:
            i = used_index.get(slot_name, 0)
            values = by_slot.get(slot_name, [])
            row.append(values[i] if i < len(values) else "")
            used_index[slot_name] = i + 1
        writer.writerow(row)

    if missing:
        raise MissingDraftableIdError(
            f"{len(set(missing))} player(s) are missing DK's real upload ID (e.g. {missing[0]}) — "
            "this slate was likely captured/built before this field existed. Rebuild the slate to pick up "
            "real draftable IDs, then export again."
        )

    return buf.getvalue()
