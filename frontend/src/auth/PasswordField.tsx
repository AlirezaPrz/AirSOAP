import { useId, useState, type KeyboardEvent, type ReactNode } from 'react'
import { AlertIcon, EyeIcon, EyeOffIcon } from '../components/icons'
import { inputClass, labelClass } from '../components/ui'

interface PasswordFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: 'current-password' | 'new-password'
  /** Extra help under the field, linked with aria-describedby. */
  hint?: ReactNode
}

/** A password input that can be revealed while typing, and warns when Caps Lock is on. */
export function PasswordField({ label, value, onChange, autoComplete, hint }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)
  const [capsLock, setCapsLock] = useState(false)
  const id = useId()
  const describedBy = [hint ? `${id}-hint` : '', capsLock ? `${id}-caps` : ''].filter(Boolean).join(' ') || undefined

  const readCapsLock = (event: KeyboardEvent<HTMLInputElement>) => setCapsLock(event.getModifierState('CapsLock'))

  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      <div className="relative mt-2">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          autoCapitalize="none"
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={readCapsLock}
          onKeyUp={readCapsLock}
          onBlur={() => setCapsLock(false)}
          aria-describedby={describedBy}
          className={`${inputClass} pr-12`}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          aria-pressed={visible}
          className="absolute inset-y-1 right-1 flex w-10 items-center justify-center rounded-(--radius-control) text-ink-3 transition-colors hover:bg-sunken hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
        >
          {visible ? <EyeOffIcon className="size-[18px]" /> : <EyeIcon className="size-[18px]" />}
        </button>
      </div>
      {capsLock && (
        <p id={`${id}-caps`} className="mt-2 flex items-center gap-1.5 text-sm font-medium text-warn-ink">
          <AlertIcon className="size-4" /> Caps Lock is on
        </p>
      )}
      {hint && (
        <div id={`${id}-hint`} className="mt-2 text-sm text-ink-3">
          {hint}
        </div>
      )}
    </div>
  )
}
