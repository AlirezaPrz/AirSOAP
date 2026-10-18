/** One tile of the waves; the drawing holds two tiles side by side so it can loop seamlessly. */
const TILE = 1200
const HEIGHT = 320
const LINES = 6

/** Each line is two sine waves whose periods divide the tile, so its ends meet the next tile. */
function wavePath(index: number): string {
  const base = 90 + index * 38
  const amplitude = 14 + 9 * Math.sin(index * 1.3 + 0.4)
  const phase = index * 0.7
  const points: string[] = []
  for (let x = 0; x <= TILE * 2; x += 24) {
    const t = (x / TILE) * Math.PI * 2
    const y = base + amplitude * Math.sin(t + phase) + amplitude * 0.45 * Math.sin(t * 3 - phase * 1.6)
    points.push(`${x} ${y.toFixed(1)}`)
  }
  return `M${points.join('L')}`
}

const PATHS = Array.from({ length: LINES }, (_, index) => wavePath(index))

/**
 * The quiet ground behind the clinical and admin workspaces: soft glows and a few lines of air
 * drifting along the bottom. Decoration only, behind everything, never in the way of a click.
 */
export function Ambient({ active = false }: { active?: boolean }) {
  return (
    <div aria-hidden data-active={active || undefined} className="ambient pointer-events-none fixed inset-0 -z-10">
      <div className="ambient-waves absolute inset-x-0 bottom-0 h-[42vh] min-h-56 overflow-hidden lg:left-[272px]">
        <svg
          viewBox={`0 0 ${TILE * 2} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="absolute bottom-0 left-0 h-full w-[200%] min-w-[2400px]"
        >
          {PATHS.map((d, index) => (
            <path
              key={d}
              d={d}
              fill="none"
              stroke="var(--color-brand-navy)"
              strokeOpacity={0.05 + index * 0.012}
              strokeWidth="1.25"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
      </div>
    </div>
  )
}
