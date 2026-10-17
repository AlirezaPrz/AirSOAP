import type { SVGProps } from 'react'

const NAVY = 'var(--color-brand-navy)'
const TEAL = 'var(--color-brand-teal)'

// Waveform bars inside the speech bubble: x position and half-height.
const WAVE: [number, number][] = [
  [17, 5],
  [26, 11],
  [35, 18],
  [44, 23],
  [53, 16],
  [62, 9],
]

/**
 * The AirSOAP mark: a speech bubble carrying a voice waveform, overlapped by a clinical note
 * with a check. Drawn as vector art from the project logo so it stays sharp at every size.
 */
export function LogoMark({ title, ...props }: SVGProps<SVGSVGElement> & { title?: string }) {
  return (
    <svg
      viewBox="0 0 120 100"
      xmlns="http://www.w3.org/2000/svg"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      {...props}
    >
      {title && <title>{title}</title>}
      {/* Speech bubble with its tail at the lower left */}
      <path
        d="M40 6a37 37 0 1 1-17.6 69.6L7 82l5.2-15.1A37 37 0 0 1 40 6Z"
        fill="#fff"
        stroke={NAVY}
        strokeWidth="6.5"
        strokeLinejoin="round"
      />
      {WAVE.map(([x, h]) => (
        <line key={x} x1={x} x2={x} y1={43 - h} y2={43 + h} stroke={TEAL} strokeWidth="5.5" strokeLinecap="round" />
      ))}
      {/* The clinical note, folded corner in teal */}
      <path
        d="M70 22a6 6 0 0 1 6-6h26l12 12v58a6 6 0 0 1-6 6H76a6 6 0 0 1-6-6Z"
        fill="#fff"
        stroke={NAVY}
        strokeWidth="6.5"
        strokeLinejoin="round"
      />
      <path d="M102 16v12h12Z" fill={TEAL} stroke={TEAL} strokeWidth="2" strokeLinejoin="round" />
      <path d="M84.5 25v14M77.5 32h14" stroke={TEAL} strokeWidth="5" strokeLinecap="round" />
      <path d="M78 49h26M78 59h26M78 69h13" stroke={NAVY} strokeWidth="4.5" strokeLinecap="round" />
      <circle cx="101" cy="75" r="10" fill={TEAL} />
      <path d="m96.2 75.2 3.2 3.2 6.2-6.4" fill="none" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** The name as the logo sets it: "Air" in navy, "SOAP" in teal, heavy and tight. */
export function LogoWordmark({ className = '' }: { className?: string }) {
  return (
    <span className={`font-bold tracking-[-0.02em] ${className}`}>
      <span className="text-brand-navy">Air</span>
      <span className="text-brand-teal">SOAP</span>
    </span>
  )
}

/** Mark and name side by side, for app headers and the sidebar. */
export function Logo({ subtitle, size = 'md' }: { subtitle?: string; size?: 'sm' | 'md' }) {
  const mark = size === 'sm' ? 'h-7' : 'h-9'
  const word = size === 'sm' ? 'text-[19px]' : 'text-[22px]'
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark className={`${mark} w-auto shrink-0`} />
      <span className="leading-tight">
        <LogoWordmark className={`block ${word}`} />
        {subtitle && <span className="block text-sm text-ink-3">{subtitle}</span>}
      </span>
    </span>
  )
}
