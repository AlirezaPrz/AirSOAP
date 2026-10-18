from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from ..ai.base import AiClient
from ..ai.jobs import AiJobManager
from ..auth.service import AuthService
from ..config import Settings
from ..db import Database
from ..jobs import JobManager
from ..live import LiveTranscriber
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
    ai_client: AiClient
    ai_jobs: AiJobManager
    db: Database
    auth: AuthService
    live: LiveTranscriber | None = None

    async def discard_owner(self, owner_id: str) -> None:
        """Stop this account's recording and drop its jobs and results (sign-out, expiry, disabling)."""
        await self.sessions.discard_owner(owner_id)
        await self.jobs.discard_owner(owner_id)
        await self.ai_jobs.discard_owner(owner_id)


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]
