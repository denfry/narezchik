import json, os, sys

import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remote


def test_plan_files_collects_source_assets_and_plan(tmp_path):
    plan = {"source": {"path": "input/a.mp4"},
            "assets": [{"id": "m", "path": "assets/music/x.mp3"}, {"id": "u", "url": "http://x"}],
            "audio_events": [], "visual_events": [{"type": "broll", "asset_ref": "m"}]}
    files = remote.plan_files("edit-plans/a_short01.json", plan)
    assert files == ["edit-plans/a_short01.json", "input/a.mp4", "assets/music/x.mp3"]


def test_plan_files_skips_http_source():
    plan = {"source": {"path": "https://host/v.mp4"}, "assets": []}
    assert remote.plan_files("edit-plans/p.json", plan) == ["edit-plans/p.json"]


def test_plan_files_includes_enabled_tail_card_image():
    plan = {"source": {"path": "input/a.mp4"}, "assets": [],
            "render_settings": {"tail_card": {"enabled": True, "image_path": "cta/end.png"},
                                "cover_frame": {"enabled": False, "image_path": "temp/cover.png"}}}
    assert remote.plan_files("edit-plans/p.json", plan) == [
        "edit-plans/p.json", "input/a.mp4", "cta/end.png"]


def test_plan_files_includes_external_speech_segments():
    # CTA-зазывалка — сегмент монтажа со своим звуком (speech_edits[].source_path), не asset:
    # без неё рендер на VDS падает «внешний сегмент ссылается на отсутствующий файл»
    plan = {"source": {"path": "input/a.mp4"}, "assets": [],
            "speech_edits": [{"id": "s1"},
                             {"id": "cta_a", "source_path": "cta/invite.mp4"},
                             {"id": "cta_b", "source_path": "cta/invite.mp4"}]}
    assert remote.plan_files("edit-plans/p.json", plan) == [
        "edit-plans/p.json", "input/a.mp4", "cta/invite.mp4"]


def test_plan_files_includes_lut_file():
    # LUT задаётся именем в render_settings, файл лежит в assets/luts/ — вне CODE_DIRS;
    # без него рендер на VDS падает в самом конце подготовки: «LUT не найден»
    plan = {"source": {"path": "input/a.mp4"}, "assets": [], "render_settings": {"lut": "warm_film"}}
    assert remote.plan_files("edit-plans/p.json", plan) == [
        "edit-plans/p.json", "input/a.mp4", "assets/luts/warm_film.cube"]
    for off in ("", "none", None):
        plan["render_settings"]["lut"] = off
        assert remote.plan_files("edit-plans/p.json", plan) == ["edit-plans/p.json", "input/a.mp4"]


def test_parse_md5sum_output():
    out = ("d41d8cd98f00b204e9800998ecf8427e  scripts/pipeline/a.py\n"
           "8aa4bd90ab1842af0cec4f3639551af2  assets/fonts/Montserrat ExtraBold.ttf\n\n")
    assert remote.parse_md5sum(out) == {
        "scripts/pipeline/a.py": "d41d8cd98f00b204e9800998ecf8427e",
        "assets/fonts/Montserrat ExtraBold.ttf": "8aa4bd90ab1842af0cec4f3639551af2"}


def test_code_upload_plan_rewrites_changed_and_missing_files():
    # код сверяется содержимым: изменённый .py длиннее старого, и докачка хвоста по размеру
    # дописывала конец нового файла к старому (15.09.2026, SyntaxError в render_short.py на VDS)
    local = {"scripts/pipeline/a.py": "1", "scripts/pipeline/b.py": "2", "schemas/s.json": "3"}
    remote_hashes = {"scripts/pipeline/a.py": "1", "scripts/pipeline/b.py": "old", "scripts/pipeline/gone.py": "9"}
    assert remote.code_upload_plan(local, remote_hashes) == ["schemas/s.json", "scripts/pipeline/b.py"]


def test_upload_mode_uses_remote_copy_when_local_is_gone():
    assert remote.upload_mode(10, 10) == "skip"
    assert remote.upload_mode(10, 10, force=True) == "write"
    assert remote.upload_mode(10, 4) == "append"
    assert remote.upload_mode(10, None) == "write"
    assert remote.upload_mode(None, 18) == "skip"
    assert remote.upload_mode(None, None) == "missing"


def test_runner_env_prefix_and_validation():
    assert remote.runner_env() == ""
    assert remote.runner_env(cpus="5", mem="8g") == "MK_CPUS=5 MK_MEM=8g "
    for bad in ("8g; rm -rf /", "lots"):
        with pytest.raises(ValueError):
            remote.runner_env(mem=bad)
    with pytest.raises(ValueError):
        remote.runner_env(cpus="3 && reboot")


def test_job_id_is_slug_and_unique():
    a, b = remote.new_job_id("transcribe", "clip 31/08"), remote.new_job_id("transcribe", "clip 31/08")
    assert a.startswith("transcribe-clip_31_08-") and a != b
    assert all(c.isalnum() or c in "-_" for c in a)


def test_transcribe_command_forces_cpu():
    cmd = remote.transcribe_command("temp/x_audio.wav", "x", "small", "ru")
    assert cmd[:2] == ["python", "scripts/pipeline/transcribe.py"]
    assert "--device" in cmd and cmd[cmd.index("--device") + 1] == "cpu"
    assert "cuda" not in cmd
