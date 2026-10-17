import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { Button } from './Button'
import { Alert } from './ui'

// The dialog genuinely floats above the page, so it is the one surface with a shadow.
const DIALOG_SHADOW = 'shadow-[0_24px_64px_-16px_rgb(18_26_23/0.35)]' // taste-skill-ignore

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'

interface DialogProps {
  title: string
  children: ReactNode
  /** The buttons row, right-aligned on wide screens. */
  footer: ReactNode
  error?: string | null
  /** Escape closes the dialog unless busy. */
  busy?: boolean
  onClose: () => void
  /** Receives focus when the dialog opens; the first focusable element otherwise. */
  initialFocus?: RefObject<HTMLElement | null>
  /** Wraps body and footer, e.g. in a form so Enter submits. */
  as?: (content: ReactNode) => ReactNode
}

/** Modal with focus trap, Escape to close, and focus restore on close. */
export function Dialog({ title, children, footer, error, busy = false, onClose, initialFocus, as }: DialogProps) {
  const titleId = useId()
  const bodyId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    const target = initialFocus?.current ?? dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE)
    target?.focus()
    return () => previouslyFocused?.focus()
    // Only on open (the ref object never changes): focus must not jump while the person types.
  }, [initialFocus])

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && !busy) {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab' || !dialogRef.current) return
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const content = (
    <>
      <div id={bodyId} className="mt-3 space-y-4 text-[15px] text-ink-2">
        {children}
      </div>
      {error && (
        <div className="mt-4">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      <div className="mt-8 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">{footer}</div>
    </>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center overflow-y-auto bg-ink/45 p-4 sm:items-center">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onKeyDown={handleKeyDown}
        className={`w-full max-w-lg rounded-(--radius-panel) bg-surface p-6 ${DIALOG_SHADOW} sm:p-8`}
      >
        <h2 id={titleId} className="text-xl font-semibold tracking-[-0.01em] text-ink">
          {title}
        </h2>
        {as ? as(content) : content}
      </div>
    </div>
  )
}

interface ConfirmDialogProps {
  title: string
  children: ReactNode
  confirmLabel: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  busy?: boolean
  error?: string | null
  onConfirm: () => void
  onCancel: () => void
}

/** Modal confirmation. Focus starts on Cancel, the safe choice. */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  tone = 'primary',
  busy = false,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  return (
    <Dialog
      title={title}
      error={error}
      busy={busy}
      onClose={onCancel}
      initialFocus={cancelRef}
      footer={
        <>
          <Button ref={cancelRef} variant="secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button variant={tone} onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  )
}
