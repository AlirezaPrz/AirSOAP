"""JobManager unit tests that need direct control of time."""

import asyncio

import pytest
from helpers import FakeTranscriber

from airsoap.audio import synth_tone, write_wav
from airsoap.errors import ApiError
from airsoap.jobs import JobManager, JobStatus
from airsoap.storage import WorkDir

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.fixture
def storage(tmp_path):
    work = WorkDir(tmp_path / "work")
    work.prepare()
    return work


def _audio(storage: WorkDir):
    path = storage.new_audio_path()
    write_wav(path, synth_tone(500))
    return path


async def test_finished_jobs_expire_after_ttl(storage):
    clock = Clock()
    jobs = JobManager(
        transcriber=FakeTranscriber(), storage=storage, ttl_seconds=60, max_pending=4, clock=clock
    )
    await jobs.start()
    try:
        job = jobs.submit(_audio(storage), 500)
        await _wait_until(lambda: job.status is JobStatus.COMPLETED)
        clock.now += 59
        assert jobs.get(job.id).result is not None
        clock.now += 1
        with pytest.raises(ApiError) as excinfo:
            jobs.get(job.id)
        assert excinfo.value.code == "JOB_NOT_FOUND"
    finally:
        await jobs.close()


async def test_close_cancels_pending_jobs_and_deletes_audio(storage):
    transcriber = FakeTranscriber(gated=True)
    jobs = JobManager(transcriber=transcriber, storage=storage, ttl_seconds=60, max_pending=4)
    await jobs.start()
    first = jobs.submit(_audio(storage), 500)
    second = jobs.submit(_audio(storage), 500)
    await _wait_until(lambda: first.status is JobStatus.PROCESSING)
    await jobs.close()
    assert first.status is JobStatus.CANCELLED
    assert second.status is JobStatus.CANCELLED
    assert list(storage.root.glob("rec-*.wav")) == []


async def test_cancel_before_task_runs_still_finishes_job(storage):
    jobs = JobManager(transcriber=FakeTranscriber(), storage=storage, ttl_seconds=60, max_pending=4)
    await jobs.start()
    try:
        job = jobs.submit(_audio(storage), 500)
        # Let the worker pick the job up and create its task, but not run it yet.
        while job.task is None:
            await asyncio.sleep(0)
        await jobs.cancel(job.id)
        assert job.status is JobStatus.CANCELLED
        assert not job.audio_path.exists()
    finally:
        await jobs.close()


async def test_submit_requires_start(storage):
    jobs = JobManager(transcriber=FakeTranscriber(), storage=storage, ttl_seconds=60, max_pending=4)
    with pytest.raises(RuntimeError):
        jobs.submit(_audio(storage), 500)
