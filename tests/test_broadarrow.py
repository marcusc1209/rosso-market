

def test_lots_awaiting_results_are_read_again_for_a_while():
    from datetime import date
    from pipeline.sources import broadarrow as ba
    today = date(2026, 11, 6)
    assert ba.retry_awaiting("past lot with only an estimate: no result published", today)          # stored before the fix
    assert ba.retry_awaiting("past lot with only an estimate: no result published|2026-11-01", today)
    assert not ba.retry_awaiting("past lot with only an estimate: no result published|2026-08-01", today)  # gave up
    assert not ba.retry_awaiting("sold, price not published", today)                                  # never published
    assert ba.awaiting_since("past lot ...|2026-11-01") == "2026-11-01"
