from __future__ import annotations

import json
from types import SimpleNamespace

from recsys_offline import train_all


def test_train_all_uses_media_thresholds_and_preserves_model_on_gate_failure(
    tmp_path, monkeypatch,
):
    data_dir = tmp_path / "data"
    publish_dir = tmp_path / "published"
    data_dir.mkdir()
    (data_dir / "collections_book.csv").write_text(
        "user_id,subject_id,ctype,rate\n1,10,2,9\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command, check):
        assert check is False
        commands.append(command)
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(train_all.subprocess, "run", fake_run)
    monkeypatch.setattr(train_all.sys, "argv", [
        "train_all",
        "--data-dir", str(data_dir),
        "--publish-dir", str(publish_dir),
        "--media", "book",
    ])

    assert train_all.main() == 0

    command = commands[0]
    assert command[command.index("--min-user") + 1] == "5"
    assert command[command.index("--min-item") + 1] == "4"
    assert command[command.index("--export-model") + 1] == "auto"
    assert not (publish_dir / "i2i_book.json").exists()
    manifest = json.loads((publish_dir / "cf_models.json").read_text(encoding="utf-8"))
    assert manifest["models"] == {}
    assert "quality gate" in manifest["skipped"]["book"]


def test_incremental_manifest_preserves_existing_media_and_reports_hard_failure(
    tmp_path, monkeypatch,
):
    data_dir = tmp_path / "data"
    publish_dir = tmp_path / "published"
    data_dir.mkdir()
    publish_dir.mkdir()
    (data_dir / "collections_music.csv").write_text(
        "user_id,subject_id,ctype,rate\n1,10,2,9\n",
        encoding="utf-8",
    )
    (publish_dir / "i2i_anime.json").write_text(json.dumps({
        "meta": {"model": "als", "n_users": 20},
        "items": {"1": [[2, 0.5]]},
    }), encoding="utf-8")

    monkeypatch.setattr(
        train_all.subprocess,
        "run",
        lambda _command, check: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(train_all.sys, "argv", [
        "train_all",
        "--data-dir", str(data_dir),
        "--publish-dir", str(publish_dir),
        "--media", "music",
    ])

    assert train_all.main() == 1

    manifest = json.loads((publish_dir / "cf_models.json").read_text(encoding="utf-8"))
    assert manifest["models"]["anime"]["model"] == "als"
    assert manifest["skipped"]["music"] == "training failed (1)"
