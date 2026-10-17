import { useCallback, useState, type ReactNode } from 'react'
import type { Account, AuthApi } from '../api/auth'
import { AccountSummary, MobileSignOut, type AccountInfo } from '../components/AccountSummary'
import { Logo } from '../components/Logo'
import { AccountsScreen } from './AccountsScreen'
import { countWhere, type Filter } from './filters'

const OVERVIEW: { filter: Filter; label: string; hint: string }[] = [
  { filter: 'active', label: 'Active', hint: 'Can sign in' },
  { filter: 'temporary', label: 'Temporary password', hint: 'Not signed in yet' },
  { filter: 'disabled', label: 'Disabled', hint: 'Blocked from signing in' },
]

const TASKS = [
  'New account creates a sign-in with a temporary password. Hand it over in person.',
  'Edit changes a name or roles, or turns off Can sign in to disable someone at once.',
  'Reset password signs the person out and gives you a new temporary password.',
]

/** Account counts that double as shortcuts to the matching list filter. */
function Overview({
  accounts,
  filter,
  onFilter,
}: {
  accounts: Account[] | null
  filter: Filter
  onFilter: (filter: Filter) => void
}) {
  return (
    <section aria-labelledby="admin-overview">
      <h2 id="admin-overview" className="px-3 text-sm font-semibold text-ink-2">
        Overview
      </h2>
      <ul className="mt-2 space-y-0.5">
        {OVERVIEW.map((item) => {
          const active = filter === item.filter
          const count = accounts ? countWhere(accounts, item.filter) : null
          return (
            <li key={item.filter}>
              <button
                type="button"
                aria-pressed={active}
                aria-label={`${item.label}: ${count ?? 'loading'}. ${active ? 'Show all accounts' : 'Show only these'}`}
                onClick={() => onFilter(active ? 'all' : item.filter)}
                className={`flex w-full items-center justify-between gap-3 rounded-(--radius-control) px-3 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-accent ${
                  active ? 'bg-surface' : 'hover:bg-control-hover'
                }`}
              >
                <span>
                  <span className={`block text-[15px] text-ink ${active ? 'font-medium' : ''}`}>{item.label}</span>
                  <span className="block text-sm text-ink-3">{item.hint}</span>
                </span>
                <span
                  className={`text-xl font-semibold tabular-nums ${
                    item.filter === 'temporary' && count ? 'text-warn-ink' : 'text-ink'
                  }`}
                >
                  {count ?? '–'}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Guide() {
  return (
    <section aria-label="What you can do here" className="px-3">
      <h2 className="text-sm font-semibold text-ink-2">What you can do here</h2>
      <ul className="mt-2 space-y-2 text-sm text-ink-2">
        {TASKS.map((task) => (
          <li key={task} className="flex gap-2.5">
            <span aria-hidden className="mt-[7px] size-1.5 shrink-0 rounded-full bg-accent" />
            {task}
          </li>
        ))}
      </ul>
      <p className="mt-4 text-sm text-ink-3">
        Locked out yourself? On the device, run{' '}
        <code className="rounded-sm bg-surface px-1 py-0.5 text-[13px] whitespace-nowrap text-ink">python -m airsoap.admin</code> (backend
        README, Recovery).
      </p>
    </section>
  )
}

function AdminShell({ account, sidebar, children }: { account: AccountInfo; sidebar: ReactNode; children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-ground text-ink">
      <aside className="fixed inset-y-0 left-0 hidden w-[272px] flex-col gap-8 overflow-y-auto bg-rail px-5 py-7 lg:flex">
        <div className="px-3">
          <Logo subtitle="Administration" />
        </div>
        {sidebar}
        <div className="mt-auto">
          <AccountSummary account={account} />
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

/** The administrator role: the account list, with an overview and a short guide in the sidebar. */
export function AdminArea({
  auth,
  account,
  currentUserId,
}: {
  auth: AuthApi
  account: AccountInfo
  currentUserId: string
}) {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const onAccounts = useCallback((list: Account[]) => setAccounts(list), [])

  return (
    <AdminShell
      account={account}
      sidebar={
        <>
          <Overview accounts={accounts} filter={filter} onFilter={setFilter} />
          <Guide />
        </>
      }
    >
      <AccountsScreen
        auth={auth}
        currentUserId={currentUserId}
        filter={filter}
        onFilter={setFilter}
        onAccounts={onAccounts}
      />
      <div className="mt-10 lg:hidden">
        <Guide />
      </div>
    </AdminShell>
  )
}
