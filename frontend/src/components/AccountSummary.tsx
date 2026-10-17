import { ROLE_LABELS, type Role } from '../api/auth'
import { Button } from './Button'

export interface AccountInfo {
  name: string
  email: string
  role: Role
  onSignOut: () => void
}

/** Who is signed in, in which role, and the way out. For the desktop sidebar. */
export function AccountSummary({ account }: { account: AccountInfo }) {
  return (
    <section aria-label="Signed in" className="rounded-(--radius-panel) bg-surface p-4">
      <p className="truncate text-[15px] font-semibold text-ink">{account.name}</p>
      <p className="truncate text-sm text-ink-3">{account.email}</p>
      <p className="mt-2 text-sm text-ink-2">{ROLE_LABELS[account.role]}</p>
      <Button variant="secondary" onClick={account.onSignOut} className="mt-3 w-full">
        Sign out
      </Button>
    </section>
  )
}

/** Compact sign-out for the phone header. */
export function MobileSignOut({ account }: { account: AccountInfo }) {
  return (
    <Button variant="ghost" onClick={account.onSignOut} className="-mr-2 h-9 px-3 text-sm">
      Sign out
    </Button>
  )
}
