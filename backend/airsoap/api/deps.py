from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from ..config import Settings
from ..jobs import JobManager
from ..recording import Recorder
from ..sessions import RecordingSessions
from ..storage import WorkDir
from ..transcription import Transcriber


@dataclass(frozen=True)
class Services:
    settings: Settings
    storage: WorkDir
    recorder: Recorder
    transcriber: Transcriber
    jobs: JobManager
    sessions: RecordingSessions


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]
