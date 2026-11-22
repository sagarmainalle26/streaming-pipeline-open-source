from datetime import datetime
from zoneinfo import ZoneInfo

from source_capture.metrics import TokenStat, events_from_payload, exchange_to_receive_ms, note_event, session_period

IST = ZoneInfo("Asia/Kolkata")


def test_session_period_open():
    ts = datetime(2026, 9, 21, 9, 20, tzinfo=IST)
    assert session_period(ts) == "open"


def test_session_period_normal():
    ts = datetime(2026, 9, 21, 14, 0, tzinfo=IST)
    assert session_period(ts) == "normal"


def test_events_from_single_dict():
    payload = {"token": "26009", "last_traded_price": 1}
    events = events_from_payload(payload)
    assert len(events) == 1
    assert events[0]["token"] == "26009"


def test_events_from_nested_list():
    payload = {"events": [{"token": "1"}, {"token": "2"}]}
    assert len(events_from_payload(payload)) == 2


def test_note_event_out_of_order_and_jump():
    stat = TokenStat()
    note_event(stat, {"last_traded_price": 100, "sequence_number": 10, "exchange_timestamp": 2000})
    note_event(stat, {"last_traded_price": 100, "sequence_number": 12, "exchange_timestamp": 1000})
    note_event(stat, {"last_traded_price": 0, "sequence_number": 13, "exchange_timestamp": 2000})
    assert stat.seq_jumps == 1
    assert stat.exchange_ts_backwards == 1
    assert stat.duplicate_exchange_ts == 1
    assert stat.invalid_ltp == 1


def test_exchange_to_receive_ms():
    exchange_ms = 1_700_000_000_000
    receive = 1_700_000_000.250
    lag = exchange_to_receive_ms(exchange_ms, receive)
    assert lag is not None
    assert abs(lag - 250.0) < 0.01
