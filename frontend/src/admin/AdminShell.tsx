import type { ReactNode } from 'react'
import type { AuthApi } from '../api/auth'
import { AccountSummary, MobileSignOut, type AccountInfo } from '../components/AccountSummary'
import { Logo } from '../components/Logo'
import { AccountsScreen } from './AccountsScreen'

const ROLES: { name: string; can: string; cannot: string }[] = [
  {
    name: 'Clinician',
    can: 'Records encounters and drafts and reviews notes.',
    cannot: 'Cannot see or change accounts.',
  },
  {
    name: 'Administrator',
    can: 'Creates accounts, changes roles, disables access and resets passwords.',
    cannot: 'Cannot see or start encounters.',
  },
]

/**
 * Reference the account list cannot show: what each role allows, and what to do when no
 * administrator can sign in.
 */
function AdminReference() {
  return (
    <div className="space-y-7 px-3 text-sm">
      <section aria-labelledby="admin-roles">
        <h2 id="admin-roles" className="font-semibold text-ink">
          Roles
        </h2>
        <dl className="mt-2 space-y-3">
          {ROLES.map((role) => (
            <div key={role.name}>
              <dt className="font-medium text-ink-2">{role.name}</dt>
              <dd className="text-ink-3">
                {role.can} {role.cannot}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-ink-3">
          Someone with both roles picks one each time they sign in.
        </p>
      </section>

      <section aria-labelledby="admin-recovery">
        <h2 id="admin-recovery" className="font-semibold text-ink">
          If no administrator can sign in
        </h2>
        <p className="mt-2 text-ink-3">
          Someone with access to the device can reset a password or restore the administrator role with{' '}
          <code className="rounded-sm bg-surface px-1 py-0.5 text-[13px] whitespace-nowrap text-ink">
            python -m airsoap.admin
          </code>
          . The steps are under Recovery in the backend README.
        </p>
      </section>
    </div>
  )
}

function AdminShell({ account, children }: { account: AccountInfo; children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-ground text-ink">
      <aside className="fixed inset-y-0 left-0 hidden w-[272px] flex-col gap-8 overflow-y-auto wash-soft px-5 py-7 lg:flex">
        <div className="px-3">
          <Logo subtitle="Administration" />
        </div>
        <AdminReference />
        <div className="mt-auto">
          <AccountSummary account={account} />
        </div>
      </aside>

      <header className="wash-soft px-5 py-4 lg:hidden">
        <div className="flex items-center justify-between gap-4">
          <Logo size="sm" subtitle="Administration" />
          <MobileSignOut account={account} />
        </div>
      </header>

      <main className="min-w-0 px-5 py-8 sm:px-8 lg:ml-[272px] lg:px-12 lg:py-12">
        <div className="mx-auto max-w-[1120px]">{children}</div>
        <div className="mx-auto mt-12 max-w-[1120px] lg:hidden">
          <AdminReference />
        </div>
      </main>
    </div>
  )
}

/** The administrator role: the account list, with role and recovery reference alongside. */
export function AdminArea({
  auth,
  account,
  currentUserId,
}: {
  auth: AuthApi
  account: AccountInfo
  currentUserId: string
}) {
  return (
    <AdminShell account={account}>
      <AccountsScreen auth={auth} currentUserId={currentUserId} />
    </AdminShell>
  )
}
