import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ToastContext, type ToastMessage } from '../lib/toast'
import { CheckIcon, InfoIcon, XIcon } from './icons'

interface ActiveToast extends ToastMessage {
  id: number
}

const TOAST_MS = 4500

function ToastItem({ toast, onDismiss }: { toast: ActiveToast; onDismiss: (id: number) => void }) {
  const [hovered, setHovered] = useState(false)
  useEffect(() => {
    if (hovered) return
    const timer = setTimeout(() => onDismiss(toast.id), TOAST_MS)
    return () => clearTimeout(timer)
  }, [hovered, toast.id, onDismiss])

  const Icon = toast.tone === 'info' ? InfoIcon : CheckIcon
  return (
    <li
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="toast-in pointer-events-auto flex items-center gap-3 rounded-(--radius-panel) bg-brand-navy py-3 pr-2 pl-4 text-[15px] text-white shadow-[0_12px_32px_-12px_rgb(9_48_75/0.55)]"
    >
      <span
        aria-hidden
        className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-teal text-white"
      >
        <Icon className="size-3.5" strokeWidth={3} />
      </span>
      <span className="flex-1">{toast.text}</span>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss"
        className="flex size-8 items-center justify-center rounded-full text-white/70 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-2 focus-visible:outline-white"
      >
        <XIcon className="size-4" />
      </button>
    </li>
  )
}

/** Short confirmations in the lower corner, announced politely to screen readers. */
export function Toaster({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ActiveToast[]>([])
  const nextId = useRef(1)

  const show = useCallback((message: ToastMessage) => {
    const id = nextId.current++
    setToasts((list) => [...list.slice(-2), { ...message, id }])
  }, [])
  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((t) => t.id !== id)), [])

  return (
    <ToastContext.Provider value={show}>
      {children}
      <ol
        aria-live="polite"
        aria-label="Notifications"
        className="pointer-events-none fixed inset-x-4 bottom-4 z-[60] flex flex-col items-stretch gap-2 sm:right-6 sm:bottom-6 sm:left-auto sm:w-[380px]"
      >
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </ol>
    </ToastContext.Provider>
  )
}
