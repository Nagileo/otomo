import pandas as pd

from recsys_offline.split import temporal_leave_one_out


def test_temporal_leave_one_out_holds_latest_interaction():
    frame = pd.DataFrame([
        {"user_id": 1, "subject_id": 10, "updated_at": "2026-01-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 11, "updated_at": "2026-02-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 12, "updated_at": "2026-03-01T00:00:00Z"},
        {"user_id": 2, "subject_id": 20, "updated_at": "2026-01-01T00:00:00Z"},
    ])
    train, test = temporal_leave_one_out(frame)
    assert test == {1: {12}}
    assert (1, 10) in train and (1, 11) in train
    assert (2, 20) in train
