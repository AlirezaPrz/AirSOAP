import type { ReactNode } from 'react'
import { AccountSummary, MobileSignOut, type AccountInfo } from '../components/AccountSummary'
import { Logo } from '../components/Logo'

/** Layout for the administrator role: the same rail as the clinical workspace, with one section. */
export function AdminShell({ account, children }: { account: AccountInfo; children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-ground text-ink">
      <aside className="fixed inset-y-0 left-0 hidden w-[272px] flex-col gap-8 overflow-y-auto bg-rail px-5 py-7 lg:flex">
        <div className="px-3">
          <Logo subtitle="Administration" />
        </div>
        <nav aria-label="Administration">
          <ul>
            <li>
              <span
                aria-current="page"
                className="flex h-10 items-center rounded-(--radius-control) bg-surface px-3 text-[15px] font-medium text-ink"
              >
                Accounts
              </span>
            </li>
          </ul>
        </nav>
        <div className="mt-auto space-y-5">
          <AccountSummary account={account} />
          <p className="px-3 text-sm leading-relaxed text-ink-3">
            Account details stay on this device. No patient data is kept here.
          </p>
        </div>
      </aside>

      <header className="bg-rail px-5 py-4 lg:hidden">
        <div className="flex items-center justify-between gap-4">
          <Logo size="sm" subtitle="Administration" />
          <MobileSignOut account={account} />
        </div>
      </header>

      <main className="min-w-0 px-5 py-8 sm:px-8 lg:ml-[272px] lg:px-12 lg:py-12">
        <div className="mx-auto max-w-[1120px]">{children}</div>
      </main>
    </div>
  )
}
