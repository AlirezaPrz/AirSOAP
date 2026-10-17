import { useEffect, useState } from 'react'
import consultation from '../assets/photos/doctor-talking-with-a-patient.webp'
import device from '../assets/photos/raspberry-pi-4-model-b-top.webp'
import stethoscope from '../assets/photos/doctors-stethoscope-1.webp'

interface Slide {
  image: string
  alt: string
  title: string
  body: string
  credit: string
  /** How the photo sits in its frame. */
  position: string
}

const SLIDES: Slide[] = [
  {
    image: consultation,
    alt: 'A doctor in a white coat talks with a seated patient in an exam room.',
    title: 'Look at the patient, not the keyboard.',
    body: 'AirSOAP records the conversation, so you can listen and examine while it keeps track.',
    credit: 'Photo: National Cancer Institute, public domain',
    position: '50% 30%',
  },
  {
    image: device,
    alt: 'A Raspberry Pi circuit board seen from above.',
    title: 'Everything stays in the room.',
    body: 'Transcription and note drafting run on a small computer on the desk. Nothing goes to the cloud.',
    credit: 'Photo: Laserlicht, CC BY-SA 4.0, via Wikimedia Commons',
    position: '50% 50%',
  },
  {
    image: stethoscope,
    alt: 'A stethoscope lying on a white surface.',
    title: 'You stay the clinician.',
    body: 'The draft only uses what was said. You check every line before anything is submitted.',
    credit: 'Photo: Stethoscopes, CC BY-SA 3.0, via Wikimedia Commons',
    position: '50% 55%',
  },
]

const SLIDE_MS = 7000

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/**
 * Three photographs with a line each about how AirSOAP works, advancing on their own like a
 * product page. Pauses on hover or focus, can be paused outright, and stays still for anyone
 * who prefers reduced motion.
 */
export function Showcase() {
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(() => prefersReducedMotion())
  const [hovering, setHovering] = useState(false)
  const running = !paused && !hovering

  useEffect(() => {
    if (!running) return
    const timer = setTimeout(() => setIndex((i) => (i + 1) % SLIDES.length), SLIDE_MS)
    return () => clearTimeout(timer)
  }, [index, running])

  const slide = SLIDES[index]

  return (
    <section
      aria-roledescription="carousel"
      aria-label="How AirSOAP works"
      className="flex min-h-0 flex-1 flex-col"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onFocus={() => setHovering(true)}
      onBlur={() => setHovering(false)}
    >
      <div className="relative min-h-[240px] flex-1 overflow-hidden rounded-(--radius-panel) bg-ink">
        {SLIDES.map((s, i) => (
          <img
            key={s.title}
            src={s.image}
            alt={i === index ? s.alt : ''}
            aria-hidden={i !== index}
            decoding="async"
            className={`absolute inset-0 size-full object-cover transition-[opacity,transform] duration-[1200ms] ease-(--ease-out-expo) ${
              i === index ? 'scale-100 opacity-100' : 'scale-[1.04] opacity-0'
            }`}
            style={{ objectPosition: s.position }}
          />
        ))}
        <p className="absolute right-3 bottom-2 text-[11px] text-white/80 [text-shadow:0_1px_2px_rgb(0_0_0/0.6)]">
          {slide.credit}
        </p>
      </div>

      <div aria-live={running ? 'off' : 'polite'} className="mt-6 min-h-[112px]">
        <p key={slide.title} className="arrive text-[22px] leading-snug font-semibold tracking-[-0.01em] text-balance text-brand-navy">
          {slide.title}
        </p>
        <p key={`${slide.title}-body`} className="arrive arrive-1 mt-2 max-w-md text-[15px] text-ink-2">
          {slide.body}
        </p>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <div role="group" aria-label="Choose a slide" className="flex flex-1 gap-2">
          {SLIDES.map((s, i) => (
            <button
              key={s.title}
              type="button"
              onClick={() => setIndex(i)}
              aria-label={`Slide ${i + 1} of ${SLIDES.length}: ${s.title}`}
              aria-current={i === index}
              className="group flex h-6 flex-1 items-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span className="relative h-1 w-full overflow-hidden rounded-full bg-field transition-colors group-hover:bg-ink-3/40">
                {i < index && <span className="absolute inset-0 bg-brand-navy" />}
                {i === index && (
                  <span
                    key={`${index}-${running}`}
                    className={`absolute inset-0 bg-brand-navy ${running ? 'fill' : ''}`}
                    style={running ? { animationDuration: `${SLIDE_MS}ms` } : undefined}
                  />
                )}
              </span>
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setPaused((p) => !p)}
          aria-label={paused ? 'Play slides' : 'Pause slides'}
          className="flex size-8 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-control-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
        >
          {paused ? (
            <svg viewBox="0 0 24 24" className="size-4" fill="currentColor" aria-hidden>
              <path d="M8 5.5v13a1 1 0 0 0 1.5.9l10.4-6.5a1 1 0 0 0 0-1.8L9.5 4.6A1 1 0 0 0 8 5.5Z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="size-4" fill="currentColor" aria-hidden>
              <rect x="6.5" y="5" width="4" height="14" rx="1" />
              <rect x="13.5" y="5" width="4" height="14" rx="1" />
            </svg>
          )}
        </button>
      </div>
    </section>
  )
}
