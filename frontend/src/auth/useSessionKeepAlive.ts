import { useEffect, useRef } from 'react'
import type { AuthApi, Session } from '../api/auth'

/** Refresh the session at most this often while the person is using the page. */
export const ACTIVITY_REFRESH_MS = 2 * 60_000

const ACTIVITY_EVENTS = ['pointerdown', 'keydown'] as const

/**
 * The server ends a session after 30 idle minutes, but editing a note sends nothing to the
 * server. So activity on the page (a click or a key press) refreshes the session, at most every
 * two minutes, and when the expiry time passes without activity the session is checked and,
 * if it has ended, the app signs out and clears the screen.
 */
export function useSessionKeepAlive(
  auth: AuthApi,
  session: Session | null,
  onRefreshed: (session: Session) => void,
  onEnded: () => void,
) {
  const latest = useRef({ onRefreshed, onEnded })
  useEffect(() => {
    latest.current = { onRefreshed, onEnded }
  })

  const expiresAt = session?.sessionExpiresAt
  const signedIn = session !== null

  useEffect(() => {
    if (!signedIn) return
    let lastRefresh = Date.now()
    let inFlight = false
    let stopped = false

    const check = async () => {
      if (inFlight) return
      inFlight = true
      lastRefresh = Date.now()
      try {
        const current = await auth.me()
        if (stopped) return
        if (current) latest.current.onRefreshed(current)
        else latest.current.onEnded()
      } catch {
        // The backend is unreachable: keep the screen; the next request will tell.
      } finally {
        inFlight = false
      }
    }

    const onActivity = () => {
      if (Date.now() - lastRefresh >= ACTIVITY_REFRESH_MS) void check()
    }
    for (const event of ACTIVITY_EVENTS) window.addEventListener(event, onActivity, { passive: true })

    // A moment after the expiry the server reported, ask again; it may have been extended elsewhere.
    const due = expiresAt ? new Date(expiresAt).getTime() - Date.now() + 1_000 : null
    const timer = due === null ? undefined : setTimeout(() => void check(), Math.max(due, 1_000))

    return () => {
      stopped = true
      clearTimeout(timer)
      for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, onActivity)
    }
  }, [auth, signedIn, expiresAt])
}
