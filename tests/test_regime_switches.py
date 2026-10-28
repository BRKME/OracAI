from datetime import datetime, timezone
from prod_logger import count_regime_switches


def _write(tmp_path, rows):
    p = tmp_path / "log.csv"
    p.write_text("timestamp_utc,btc_price,regime\n" +
                 "".join(f"{t},1,{r}\n" for t, r in rows))
    return p


def test_counts_changes_within_window(tmp_path):
    p = _write(tmp_path, [
        ("2026-10-01T04:40:00+00:00", "BULL"),   # вне окна
        ("2026-10-15T04:40:00+00:00", "BEAR"),
        ("2026-10-16T04:40:00+00:00", "BULL"),
        ("2026-10-18T04:40:00+00:00", "BEAR"),
        ("2026-10-21T04:40:00+00:00", "BULL"),
        ("2026-10-22T04:40:00+00:00", "BEAR"),
        ("2026-10-27T04:40:00+00:00", "BULL"),
        ("2026-10-28T04:40:00+00:00", "BULL"),
    ])
    now = datetime(2026, 10, 28, 5, tzinfo=timezone.utc)
    assert count_regime_switches(days=14, path=p, now=now) == 5


def test_missing_file_gives_none(tmp_path):
    assert count_regime_switches(path=tmp_path / "nope.csv") is None
