interface ImportMetaEnv {
  /** `http` to use the local AirSOAP backend; otherwise the mock is used. */
  readonly VITE_AIRSOAP_API?: string
  /** Optional backend origin; defaults to same origin. */
  readonly VITE_AIRSOAP_API_BASE?: string
}
