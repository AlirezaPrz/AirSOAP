"""Endpoints for the AI tasks. Each starts a job and returns at once; poll the job for the result."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response, status

from ..ai.jobs import AiJob, AiJobKind
from ..ai.schemas import ClinicalReviewRequest, SoapRequest, SpeakerRequest
from ..auth.csrf import require_same_origin
from ..auth.deps import ClinicianDep, require_clinician
from ..errors import ApiError
from .ai_schemas import AiJobAccepted, AiJobResponse
from .deps import ServicesDep

MAX_BODY_BYTES = 128_000
JobIdPath = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="AI job ID")]


async def limit_body_size(request: Request) -> None:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ApiError(413, "REQUEST_TOO_LARGE", "The transcript and note are too long for the AI model.")


# Order matters: size is checked first, then origin, then the clinician session.
router = APIRouter(
    prefix="/api/ai",
    tags=["ai"],
    dependencies=[Depends(limit_body_size), Depends(require_same_origin), Depends(require_clinician)],
)


def _accepted(job: AiJob) -> AiJobAccepted:
    return AiJobAccepted(job_id=job.id, kind=job.kind)


@router.post("/soap", status_code=status.HTTP_202_ACCEPTED)
async def start_soap(body: SoapRequest, user: ClinicianDep, services: ServicesDep) -> AiJobAccepted:
    """Draft a SOAP note from the labelled transcript. Never submits or saves anything."""
    return _accepted(services.ai_jobs.submit(AiJobKind.SOAP, body, owner_id=user.user.id))


@router.post("/clinical-review", status_code=status.HTTP_202_ACCEPTED)
async def start_clinical_review(
    body: ClinicalReviewRequest, user: ClinicianDep, services: ServicesDep
) -> AiJobAccepted:
    """Review the clinician's edited note. Returns suggestions only; the note is never changed."""
    return _accepted(services.ai_jobs.submit(AiJobKind.CLINICAL_REVIEW, body, owner_id=user.user.id))


@router.post("/speakers", status_code=status.HTTP_202_ACCEPTED)
async def start_speaker_suggestions(
    body: SpeakerRequest, user: ClinicianDep, services: ServicesDep
) -> AiJobAccepted:
    """Suggest who said each turn, from the words only. The transcript itself is never changed."""
    return _accepted(services.ai_jobs.submit(AiJobKind.SPEAKERS, body, owner_id=user.user.id))


@router.get("/jobs/{job_id}")
async def get_job(job_id: JobIdPath, user: ClinicianDep, services: ServicesDep) -> AiJobResponse:
    return AiJobResponse.from_job(services.ai_jobs.get(job_id, owner_id=user.user.id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: JobIdPath, user: ClinicianDep, services: ServicesDep) -> AiJobResponse:
    return AiJobResponse.from_job(await services.ai_jobs.cancel(job_id, owner_id=user.user.id))


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: JobIdPath, user: ClinicianDep, services: ServicesDep) -> Response:
    """Cancel if running and drop the result from memory. Safe to call for an unknown job."""
    await services.ai_jobs.delete(job_id, owner_id=user.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
