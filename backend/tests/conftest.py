import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def known_qb_stat_line():
    """Josh Allen-esque game: 275 pass yds, 3 pass TD, 1 INT, 45 rush yds, 1 rush TD.
    Expected DK points = 275*0.04 + 3*4 + 1*(-1) + 45*0.1 + 1*6 = 11+12-1+4.5+6 = 32.5
    (no 300-yard bonus since 275 < 300).
    """
    return {
        "pass_yards": 275, "pass_td": 3, "interceptions": 1,
        "rush_yards": 45, "rush_td": 1,
    }


@pytest.fixture
def known_rb_stat_line():
    """20 carries for 110 yards + 1 TD, 3 catches for 25 yards on 4 targets.
    Expected = 110*0.1 + 3(100yd bonus) + 6(TD) + 3*1(rec) + 25*0.1 = 11+3+6+3+2.5 = 25.5
    """
    return {
        "rush_yards": 110, "rush_td": 1, "receptions": 3, "targets": 4, "rec_yards": 25,
    }
