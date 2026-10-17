import { createHttpAuthApi, createMockAuthApi, type AuthApi } from './auth'
import type { AirSoapApi } from './client'
import { createHttpApi } from './httpClient'
import { createMockApi } from './mockClient'
import type { SessionEndedHandler } from './request'

export interface ApiEnv {
  VITE_AIRSOAP_API?: string
  VITE_AIRSOAP_API_BASE?: string
}

/**
 * `VITE_AIRSOAP_API=http` uses the local backend; anything else (the default)
 * keeps the fully simulated mock.
 */
export function createDefaultApi(env: ApiEnv = import.meta.env, onSessionEnded?: SessionEndedHandler): AirSoapApi {
  if (env.VITE_AIRSOAP_API === 'http') {
    return createHttpApi({ baseUrl: env.VITE_AIRSOAP_API_BASE ?? '', onSessionEnded })
  }
  return createMockApi()
}

export interface Services {
  api: AirSoapApi
  auth: AuthApi
  /** Registers the one listener told when a request finds the session has ended. Returns an unsubscribe. */
  onSessionEnded(handler: SessionEndedHandler): () => void
}

/** The clinical API and the account API, sharing one "session ended" signal. */
export function createDefaultServices(env: ApiEnv = import.meta.env): Services {
  let listener: SessionEndedHandler | null = null
  const notify: SessionEndedHandler = (code) => listener?.(code)
  const http = env.VITE_AIRSOAP_API === 'http'
  return {
    api: createDefaultApi(env, notify),
    auth: http ? createHttpAuthApi({ baseUrl: env.VITE_AIRSOAP_API_BASE ?? '', onSessionEnded: notify }) : createMockAuthApi(),
    onSessionEnded(handler) {
      listener = handler
      return () => {
        if (listener === handler) listener = null
      }
    },
  }
}
