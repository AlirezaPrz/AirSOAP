from ..ai.jobs import AiJob, AiJobKind
from ..ai.schemas import ClinicalReviewResult, SoapResult, SpeakerResult
from ..jobs import JobStatus
from ..models import CamelModel
from .schemas import ErrorOut


class AiJobAccepted(CamelModel):
    job_id: str
    kind: AiJobKind


class AiJobResponse(CamelModel):
    job_id: str
    kind: AiJobKind
    status: JobStatus
    soap_result: SoapResult | None = None
    review_result: ClinicalReviewResult | None = None
    speaker_result: SpeakerResult | None = None
    error: ErrorOut | None = None

    @classmethod
    def from_job(cls, job: AiJob) -> "AiJobResponse":
        soap = job.result if isinstance(job.result, SoapResult) else None
        review = job.result if isinstance(job.result, ClinicalReviewResult) else None
        speaker = job.result if isinstance(job.result, SpeakerResult) else None
        error = None
        if job.error is not None:
            error = ErrorOut(code=job.error.code, message=job.error.message, retryable=job.error.retryable)
        return cls(
            job_id=job.id,
            kind=job.kind,
            status=job.status,
            soap_result=soap,
            review_result=review,
            speaker_result=speaker,
            error=error,
        )
