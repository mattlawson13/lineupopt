"""Gets generated lineups into a file DraftKings will actually accept an
upload of.

A from-scratch CSV (just roster-slot headers + player picks, no Entry
ID/Contest ID) is NOT reliably accepted by DK's own upload — confirmed
live (2026-09-14): a user's real upload attempt using exactly that shape
failed. The reliable path (matching how every other DFS tool that does
this works, and how DK's own "Bulk Upload" flow is documented to work)
is to fill picks into the ACTUAL file DK hands you: for a single
draft-group export it's a template with the roster-slot columns empty
and a player-ID reference table alongside; for a contest you've already
entered it also carries real Entry ID/Contest ID columns per row DK
generated for your held entries, which this app has no way to invent on
its own. Either way, starting from DK's own file and only touching the
roster-pick cells guarantees the result matches whatever DK's specific
validator wants — this app doesn't have to know that shape in advance,
or get it wrong twice.

merge_lineups_into_dk_template() is the primary function: upload the
file DK gave you, get the same file back with picks filled in.
build_dk_bulk_upload_csv() (a standalone, from-scratch file) is kept as
a fallback/preview for when no template is available, clearly not
guaranteed to upload successfully on its own.

Both use DK's real per-slate, per-roster-slot "draftableId" for the
numeric ID in each "Display Name (id)" cell — NOT the stable cross-
season "playerDkId" this app uses internally for its own player
matching (see data_sources/draftkings.py). Confirmed against a real
DK-downloaded template: the numbers DK expects (e.g. 44105012) are a
completely different, much larger namespace than playerDkId (e.g.
787661), and Showdown assigns a genuinely DIFFERENT draftableId to the
same player's CPT slot vs FLEX slot. Using the wrong one — or the
missing-ID fallback of leaving it blank — gets a row silently rejected,
so both functions raise MissingDraftableIdError rather than emit a file
that looks right but would fail at DK.
"""
from __future__ import annotations

import csv
import io
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lineup import Lineup
from app.models.slate import DraftKingsPlayer, Slate
from app.optimization.dk_rules import get_contest_rules


class MissingDraftableIdError(RuntimeError):
    pass


class TemplateFormatError(RuntimeError):
    pass


_SLOT_TOKENS = {"CPT", "QB", "RB", "WR", "TE", "FLEX", "DST"}


def _lineup_cells_by_slot(dk_by_player_id: dict, lineup: Lineup) -> tuple[dict[str, list[str]], list[str]]:
    """(slot name -> ordered list of "Name (draftableId)" cells for that
    slot, names still missing a real draftableId) for one lineup. A slot
    like "RB" or "WR" can be reused across multiple header columns (DK's
    per-slot count > 1), so callers consume this list in order rather
    than overwriting a single value.
    """
    by_slot: dict[str, list[str]] = {}
    missing: list[str] = []
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
    return by_slot, missing


def _raise_if_missing(missing: list[str]) -> None:
    if missing:
        raise MissingDraftableIdError(
            f"{len(set(missing))} player(s) are missing DK's real upload ID (e.g. {missing[0]}) — "
            "this slate was likely captured/built before this field existed. Rebuild the slate to pick up "
            "real draftable IDs, then export again."
        )


def build_dk_bulk_upload_csv(db: Session, slate: Slate, lineups: list[Lineup]) -> str:
    """Standalone fallback: a fresh CSV with just roster-slot headers and
    picks, no Entry ID/Contest ID/reference table. Not guaranteed to be
    accepted by DK's own uploader on its own — prefer
    merge_lineups_into_dk_template() with a real DK-downloaded file
    whenever one is available.
    """
    rules = get_contest_rules(slate.sport, slate.contest_type)
    header: list[str] = []
    for slot in rules.slots:
        header.extend([slot.name] * slot.count)

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate.id)).scalars().all()
    dk_by_player_id = {r.player_id: r for r in dk_rows if r.player_id}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)

    all_missing: list[str] = []
    for lineup in lineups:
        by_slot, missing = _lineup_cells_by_slot(dk_by_player_id, lineup)
        all_missing.extend(missing)
        row = []
        used_index: dict[str, int] = {}
        for slot_name in header:
            i = used_index.get(slot_name, 0)
            values = by_slot.get(slot_name, [])
            row.append(values[i] if i < len(values) else "")
            used_index[slot_name] = i + 1
        writer.writerow(row)

    _raise_if_missing(all_missing)
    return buf.getvalue()


def merge_lineups_into_dk_template(template_text: str, db: Session, slate: Slate, lineups: list[Lineup]) -> tuple[str, dict]:
    """Fills generated lineups into the ACTUAL file DraftKings gave the
    user (downloaded from DK's own "Export Player List" / contest-entry
    upload screen) — see module docstring for why this, not a from-
    scratch file, is the reliable path.

    Finds the roster-slot header row (the row containing a run of tokens
    like CPT,FLEX,FLEX,... or QB,RB,RB,WR,...) wherever it is in the
    file — DK's real per-contest template has Entry ID/Contest Name/
    Contest ID/Entry Fee columns before it; a plain draft-group export
    (what this app currently captures) doesn't. Either way, only the
    roster-slot cells are ever touched — every other column (Entry ID,
    the player-ID reference table, instructions) is preserved exactly as
    DK provided it.

    Fills existing blank rows right after the header first (these
    correspond to real entries DK already knows about, if the template
    has Entry IDs), then appends new rows — matching only the roster-slot
    columns, everything else blank — for any lineups left over once
    those run out.
    """
    rows = list(csv.reader(io.StringIO(template_text)))

    header_row_idx: int | None = None
    slot_columns: list[tuple[int, str]] = []
    for i, row in enumerate(rows):
        collecting: list[tuple[int, str]] = []
        started = False
        for j, cell in enumerate(row):
            token = cell.strip().upper()
            if token in _SLOT_TOKENS:
                started = True
                collecting.append((j, token))
            elif started:
                break
        if collecting:
            header_row_idx = i
            slot_columns = collecting
            break

    if header_row_idx is None:
        raise TemplateFormatError(
            "Couldn't find a DraftKings roster header row (e.g. CPT,FLEX,... or QB,RB,WR,...) in the uploaded file — "
            "make sure this is the file DraftKings itself gave you to download, not something else."
        )

    # The uploaded template's roster shape must match the SELECTED slate's
    # actual contest format — e.g. a Showdown template (CPT,FLEX x5) has
    # no correct way to hold a Classic 9-player lineup (QB,RB,RB,WR,WR,WR,
    # TE,FLEX,DST), or vice versa. Without this check, a mismatched
    # template would silently fill only the couple of slot names that
    # happen to overlap (e.g. just the single "FLEX" slot) and leave the
    # rest of each row blank, producing a file that looks complete but
    # is missing most of every lineup.
    rules = get_contest_rules(slate.sport, slate.contest_type)
    expected_slot_names = [s.name for s in rules.slots for _ in range(s.count)]
    found_tokens = Counter(name for _, name in slot_columns)
    if found_tokens != Counter(expected_slot_names):
        expected_str = ",".join(expected_slot_names)
        found_str = ",".join(name for _, name in slot_columns)
        raise TemplateFormatError(
            f"This file's roster columns ({found_str}) don't match the selected slate's format "
            f"({expected_str}, {slate.contest_type}) — make sure you've selected the matching slate above "
            "and uploaded the right file for it."
        )

    fillable_row_indices: list[int] = []
    i = header_row_idx + 1
    while i < len(rows) and any(c.strip() for c in rows[i]):
        fillable_row_indices.append(i)
        i += 1

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate.id)).scalars().all()
    dk_by_player_id = {r.player_id: r for r in dk_rows if r.player_id}

    slot_names_in_order = [name for _, name in slot_columns]
    lineup_cell_rows: list[list[str]] = []
    all_missing: list[str] = []
    for lineup in lineups:
        by_slot, missing = _lineup_cells_by_slot(dk_by_player_id, lineup)
        all_missing.extend(missing)
        used_index: dict[str, int] = {}
        cells = []
        for slot_name in slot_names_in_order:
            k = used_index.get(slot_name, 0)
            values = by_slot.get(slot_name, [])
            cells.append(values[k] if k < len(values) else "")
            used_index[slot_name] = k + 1
        lineup_cell_rows.append(cells)

    _raise_if_missing(all_missing)

    max_col = max(j for j, _ in slot_columns)
    li = 0
    rows_filled = 0
    for row_idx in fillable_row_indices:
        if li >= len(lineup_cell_rows):
            break
        row = rows[row_idx]
        while len(row) <= max_col:
            row.append("")
        for (col, _), cell in zip(slot_columns, lineup_cell_rows[li]):
            row[col] = cell
        rows[row_idx] = row
        li += 1
        rows_filled += 1

    insert_at = fillable_row_indices[-1] + 1 if fillable_row_indices else header_row_idx + 1
    new_rows = []
    while li < len(lineup_cell_rows):
        blank = [""] * (max_col + 1)
        for (col, _), cell in zip(slot_columns, lineup_cell_rows[li]):
            blank[col] = cell
        new_rows.append(blank)
        li += 1
    rows[insert_at:insert_at] = new_rows

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(rows)

    return out.getvalue(), {
        "rows_filled": rows_filled,
        "rows_appended": len(new_rows),
        "lineups_used": rows_filled + len(new_rows),
        "lineups_total": len(lineups),
    }
