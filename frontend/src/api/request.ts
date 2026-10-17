import { ApiRequestError, cancelledError } from './client'
import type { ApiError, ApiErrorCode } from './types'

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE'

/** Codes that mean "you are no longer signed in": the app returns to the sign-in screen. */
export const SESSION_ENDED_CODES: ReadonlySet<ApiErrorCode> = new Set<ApiErrorCode>([
  'AUTH_REQUIRED',
  'SESSION_EXPIRED',
  'ACCOUNT_DISABLED',
])

export type SessionEndedHandler = (code: ApiErrorCode) => void

export interface RequesterOptions {
  /** '' means same origin: the Vite dev proxy, or the backend serving the built frontend. */
  baseUrl?: string
  fetch?: typeof fetch
  /** Called when a request fails because the session has ended (expired, signed out elsewhere, disabled). */
  onSessionEnded?: SessionEndedHandler
}

export interface RequestConfig {
  signal?: AbortSignal
  /** false for calls where "not signed in" is an expected answer (checking the session, signing in). */
  reportSessionEnd?: boolean
}

const UNREACHABLE: ApiError = {
  code: 'UNAVAILABLE',
  message: 'The AirSOAP backend is not reachable. Check that it is running on this device.',
  retryable: true,
}

export function readError(payload: unknown, status: number): ApiError {
  const error = (payload as { error?: Partial<ApiError> } | null)?.error
  if (error && typeof error.code === 'string' && typeof error.message === 'string') {
    return { code: error.code as ApiErrorCode, message: error.message, retryable: error.retryable === true }
  }
  return { code: 'UNKNOWN', message: `The backend returned an unexpected error (HTTP ${status}).`, retryable: false }
}

/**
 * One way to call the backend. The session lives only in an HttpOnly cookie the browser
 * sends by itself; this code never sees or stores it. Every request carries the header the
 * backend requires as proof it came from this app (see backend/airsoap/auth/csrf.py).
 */
export function createRequester({
  baseUrl = '',
  fetch: fetchImpl = (input, init) => globalThis.fetch(input, init),
  onSessionEnded,
}: RequesterOptions = {}) {
  return async function request<T>(
    method: HttpMethod,
    path: string,
    body?: unknown,
    { signal, reportSessionEnd = true }: RequestConfig = {},
  ): Promise<T> {
    const headers: Record<string, string> = { 'X-AirSOAP-Request': '1' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    let response: Response
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        method,
        signal,
        headers,
        credentials: 'same-origin',
        cache: 'no-store',
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw signal?.aborted ? cancelledError() : new ApiRequestError(UNREACHABLE)
    }
    const payload: unknown = response.status === 204 ? null : await response.json().catch(() => null)
    if (!response.ok) {
      const error = readError(payload, response.status)
      if (response.status === 401 && reportSessionEnd && SESSION_ENDED_CODES.has(error.code)) {
        onSessionEnded?.(error.code)
      }
      throw new ApiRequestError(error)
    }
    return payload as T
  }
}

export type Requester = ReturnType<typeof createRequester>
