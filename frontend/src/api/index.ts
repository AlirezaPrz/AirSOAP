import type { AirSoapApi } from './client'
import { createHttpApi } from './httpClient'
import { createMockApi } from './mockClient'

export interface ApiEnv {
  VITE_AIRSOAP_API?: string
  VITE_AIRSOAP_API_BASE?: string
}

/**
 * `VITE_AIRSOAP_API=http` uses the local backend; anything else (the default)
 * keeps the fully simulated mock.
 */
export function createDefaultApi(env: ApiEnv = import.meta.env): AirSoapApi {
  if (env.VITE_AIRSOAP_API === 'http') return createHttpApi({ baseUrl: env.VITE_AIRSOAP_API_BASE ?? '' })
  return createMockApi()
}
