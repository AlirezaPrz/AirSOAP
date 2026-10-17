from pathlib import Path

import pytest

from airsoap.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        work_dir=tmp_path / "work",
        recorder="mock",
        transcriber="mock",
        mock_transcribe_seconds=0,
    )
