import type { ComponentPropsWithRef } from 'react'
import { Spinner } from './icons'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'md' | 'lg'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover active:bg-accent-ink focus-visible:outline-accent',
  secondary: 'bg-rail text-ink hover:bg-control-hover active:bg-control-active focus-visible:outline-accent',
  danger: 'bg-danger text-white hover:bg-danger-hover focus-visible:outline-danger',
  ghost: 'text-ink-2 hover:bg-rail hover:text-ink focus-visible:outline-accent',
}

const SIZES: Record<Size, string> = {
  md: 'h-10 px-4 text-[15px]',
  lg: 'h-12 px-6 text-base',
}

interface ButtonProps extends ComponentPropsWithRef<'button'> {
  variant?: Variant
  size?: Size
  loading?: boolean
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  className = '',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex shrink-0 items-center justify-center gap-2 rounded-(--radius-control) font-medium whitespace-nowrap transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-45 ${SIZES[size]} ${VARIANTS[variant]} ${className}`}
    >
      {loading && <Spinner className="size-4" />}
      {children}
    </button>
  )
}
