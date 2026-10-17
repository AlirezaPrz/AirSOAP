import os
import stat
from pathlib import Path

import pytest

from airsoap.storage import UnsafePathError, WorkDir


@pytest.fixture
def work(tmp_path: Path) -> WorkDir:
    work_dir = WorkDir(tmp_path / "work")
    work_dir.prepare()
    return work_dir


def test_prepare_creates_owner_only_directory(work: WorkDir):
    mode = stat.S_IMODE(work.root.stat().st_mode)
    assert mode == 0o700


def test_prepare_refuses_symlinked_directory(tmp_path: Path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(UnsafePathError):
        WorkDir(link).prepare()


def test_new_audio_paths_are_unique_and_inside(work: WorkDir):
    first, second = work.new_audio_path(), work.new_audio_path()
    assert first != second
    assert first.parent == work.root
    assert work.resolve(first) == first


@pytest.mark.parametrize(
    "candidate",
    [
        Path("../rec-" + "a" * 32 + ".wav"),
        Path("sub/rec-" + "a" * 32 + ".wav"),
        Path("/etc/passwd"),
        Path("/tmp/rec-" + "a" * 32 + ".wav"),
        Path("notes.txt"),
        Path("rec-XYZ.wav"),
    ],
)
def test_resolve_rejects_paths_outside_or_unnamed(work: WorkDir, candidate: Path):
    with pytest.raises(UnsafePathError):
        work.resolve(candidate)


def test_resolve_rejects_symlinked_audio_file(work: WorkDir, tmp_path: Path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"x")
    link = work.root / ("rec-" + "c" * 32 + ".wav")
    link.symlink_to(outside)
    with pytest.raises(UnsafePathError):
        work.delete(link)
    assert outside.exists()


def test_delete_and_sweep_only_touch_audio_files(work: WorkDir):
    audio = work.new_audio_path()
    audio.write_bytes(b"x")
    other = work.root / "keep.txt"
    other.write_text("keep")
    assert work.delete(audio) is True
    assert work.delete(audio) is False

    for _ in range(3):
        work.new_audio_path().write_bytes(b"x")
    assert work.sweep() == 3
    assert sorted(os.listdir(work.root)) == ["keep.txt"]
