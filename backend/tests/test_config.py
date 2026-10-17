import pytest
from pydantic import ValidationError

from airsoap import config
from airsoap.config import RecorderKind, Settings, TranscriberKind


def test_auto_uses_mocks_on_macos(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "darwin")
    settings = Settings()
    assert settings.resolved_recorder is RecorderKind.MOCK
    assert settings.resolved_transcriber is TranscriberKind.MOCK


def test_auto_uses_real_services_on_linux(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "linux")
    settings = Settings()
    assert settings.resolved_recorder is RecorderKind.ARECORD
    assert settings.resolved_transcriber is TranscriberKind.WHISPER_SERVER


def test_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRSOAP_ARECORD_DEVICE", "plughw:CARD=MICROPHONE,DEV=0")
    monkeypatch.setenv("AIRSOAP_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("AIRSOAP_RECORDER", "mock")
    settings = Settings()
    assert settings.arecord_device == "plughw:CARD=MICROPHONE,DEV=0"
    assert settings.work_dir == tmp_path
    assert settings.resolved_recorder is RecorderKind.MOCK


def test_binds_to_loopback_by_default():
    assert Settings().host == "127.0.0.1"


@pytest.mark.parametrize("device", ["-D evil", "plughw:0;rm -rf /", "", "hw 2,0"])
def test_rejects_unsafe_device_names(device):
    with pytest.raises(ValidationError):
        Settings(arecord_device=device)


def test_whisper_must_stay_on_this_device():
    with pytest.raises(ValidationError):
        Settings(whisper_url="http://192.168.4.60:8178")
    with pytest.raises(ValidationError):
        Settings(whisper_url="file:///etc/passwd")
    assert Settings(whisper_url="http://localhost:9000").whisper_url == "http://localhost:9000"


def test_whisper_runs_separately_by_default():
    settings = Settings()
    assert settings.whisper_lifecycle.value == "external"
    assert settings.whisper_idle_seconds == 20


def test_on_demand_whisper_needs_paths_and_a_loopback_port(tmp_path):
    paths = {"whisper_server_path": tmp_path / "whisper-server", "whisper_model_path": tmp_path / "m.bin"}
    ok = Settings(whisper_lifecycle="on-demand", **paths)
    assert ok.whisper_lifecycle.value == "on-demand"
    with pytest.raises(ValidationError):
        Settings(whisper_lifecycle="on-demand")  # no paths
    with pytest.raises(ValidationError):
        Settings(whisper_lifecycle="on-demand", whisper_url="http://127.0.0.1", **paths)  # no port
    with pytest.raises(ValidationError):
        Settings(
            whisper_lifecycle="on-demand",
            whisper_url="http://192.168.4.60:8178",
            allow_remote_whisper=True,
            **paths,
        )  # never spawn or trust a non-loopback server
    with pytest.raises(ValidationError):
        Settings(whisper_server_path="relative/whisper-server")


def test_relative_work_dir_is_rejected():
    with pytest.raises(ValidationError):
        Settings(work_dir="relative/dir")
