import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { ROLE_LABELS, type Account, type AuthApi, type Role, type TemporaryPassword } from '../api/auth'
import { toApiError } from '../api/client'
import { Button } from '../components/Button'
import { ConfirmDialog, Dialog } from '../components/ConfirmDialog'
import { SearchIcon, Spinner } from '../components/icons'
import { Alert, PageHeader, inputClass, labelClass } from '../components/ui'
import { FILTERS, type Filter } from './filters'

const ROLES: Role[] = ['clinician', 'admin']

const ROLE_HINTS: Record<Role, string> = {
  clinician: 'Records encounters and drafts notes.',
  admin: 'Manages accounts. Cannot see encounters.',
}

const errorMessage = (caught: unknown, fallback: string) =>
  toApiError(caught, { code: 'UNKNOWN', message: fallback, retryable: true }).message

const formatDate = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso))

/** One line on where the account stands, worded for what the administrator might do next. */
function accountStatus(account: Account): string {
  if (!account.isActive) return 'Cannot sign in until re-enabled'
  if (account.mustChangePassword) {
    return account.lastLoginAt ? 'Password was reset; not changed yet' : 'Has not signed in yet'
  }
  return account.lastLoginAt ? `Last signed in ${formatDate(account.lastLoginAt)}` : 'Has not signed in yet'
}

/** "Clinician", "Administrator" or "Administrator and clinician". */
function rolesText(account: Account): string {
  const labels = account.roles.map((role) => ROLE_LABELS[role])
  return labels.length === 2 ? `${labels[0]} and ${labels[1].toLowerCase()}` : (labels[0] ?? 'No role')
}

type DialogState =
  | { kind: 'create' }
  | { kind: 'edit'; account: Account }
  | { kind: 'reset'; account: Account }
  | { kind: 'temporary'; result: TemporaryPassword; reason: 'created' | 'reset' }

interface AccountsScreenProps {
  auth: AuthApi
  /** The administrator using the screen. */
  currentUserId: string
}

export function AccountsScreen({ auth, currentUserId }: AccountsScreenProps) {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<DialogState | null>(null)

  const [attempt, setAttempt] = useState(0)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  useEffect(() => {
    let current = true
    auth.listAccounts().then(
      (list) => {
        if (!current) return
        setAccounts(list)
        setLoadError(null)
      },
      (caught: unknown) => {
        if (current) setLoadError(errorMessage(caught, 'The account list could not be loaded.'))
      },
    )
    return () => {
      current = false
    }
  }, [auth, attempt])

  const replace = (updated: Account) =>
    setAccounts((list) => list && list.map((account) => (account.id === updated.id ? updated : account)))

  return (
    <>
      <PageHeader
        title="Accounts"
        description="Each person who uses this device needs their own account. A new account starts with a temporary password, which the person replaces the first time they sign in."
        actions={<Button onClick={() => setDialog({ kind: 'create' })}>New account</Button>}
      />

      {loadError && (
        <Alert tone="error" action={
            <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </Button>
          }>
          {loadError}
        </Alert>
      )}

      {accounts === null && !loadError && (
        <p className="flex items-center gap-2 text-[15px] text-ink-2">
          <Spinner className="size-4" /> Loading accounts…
        </p>
      )}

      {accounts && (
        <AccountFilters
          accounts={accounts}
          query={query}
          filter={filter}
          onQuery={setQuery}
          onFilter={setFilter}
        />
      )}

      {accounts && visible(accounts, query, filter).length === 0 && (
        <div className="rounded-(--radius-panel) bg-surface px-6 py-10 text-center">
          <p className="text-[15px] font-medium text-ink">No accounts match</p>
          <p className="mt-1 text-[15px] text-ink-2">Try another name or email, or show all accounts.</p>
          <Button
            variant="secondary"
            className="mt-4"
            onClick={() => {
              setQuery('')
              setFilter('all')
            }}
          >
            Show all accounts
          </Button>
        </div>
      )}

      {accounts && (
        <ul aria-label="Accounts" className="space-y-2">
          {visible(accounts, query, filter).map((account) => (
            <AccountRow
              key={account.id}
              account={account}
              isYou={account.id === currentUserId}
              onEdit={() => setDialog({ kind: 'edit', account })}
              onReset={() => setDialog({ kind: 'reset', account })}
            />
          ))}
        </ul>
      )}

      {dialog?.kind === 'create' && (
        <AccountFormDialog
          auth={auth}
          onClose={() => setDialog(null)}
          onCreated={(result) => {
            setAccounts((list) => list && [...list, result.user])
            setDialog({ kind: 'temporary', result, reason: 'created' })
          }}
        />
      )}
      {dialog?.kind === 'edit' && (
        <AccountFormDialog
          auth={auth}
          account={dialog.account}
          isYou={dialog.account.id === currentUserId}
          onClose={() => setDialog(null)}
          onSaved={(updated) => {
            replace(updated)
            setDialog(null)
          }}
        />
      )}
      {dialog?.kind === 'reset' && (
        <ResetPasswordDialog
          auth={auth}
          account={dialog.account}
          onClose={() => setDialog(null)}
          onReset={(result) => {
            replace(result.user)
            setDialog({ kind: 'temporary', result, reason: 'reset' })
          }}
        />
      )}
      {dialog?.kind === 'temporary' && (
        <TemporaryPasswordDialog result={dialog.result} reason={dialog.reason} onClose={() => setDialog(null)} />
      )}
    </>
  )
}

function visible(accounts: Account[], query: string, filter: Filter): Account[] {
  const needle = query.trim().toLowerCase()
  const test = FILTERS.find((f) => f.key === filter)?.test ?? (() => true)
  return accounts.filter(
    (a) => test(a) && (!needle || a.displayName.toLowerCase().includes(needle) || a.email.includes(needle)),
  )
}

function AccountFilters({
  accounts,
  query,
  filter,
  onQuery,
  onFilter,
}: {
  accounts: Account[]
  query: string
  filter: Filter
  onQuery: (value: string) => void
  onFilter: (value: Filter) => void
}) {
  const id = useId()
  return (
    <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
      <div className="relative md:w-80">
        <label htmlFor={`${id}-search`} className="sr-only">
          Search accounts
        </label>
        <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-[18px] -translate-y-1/2 text-ink-3" />
        <input
          id={`${id}-search`}
          type="search"
          placeholder="Search by name or email"
          autoComplete="off"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          className={`${inputClass} pl-10`}
        />
      </div>
      <div role="group" aria-label="Show" className="flex flex-wrap gap-1.5">
        {FILTERS.map(({ key, label, test }) => {
          const count = accounts.filter(test).length
          const active = filter === key
          return (
            <button
              key={key}
              type="button"
              aria-pressed={active}
              onClick={() => onFilter(key)}
              className={`flex h-9 items-center gap-2 rounded-(--radius-control) px-3 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                active ? 'bg-accent text-white' : 'bg-rail text-ink-2 hover:bg-control-hover hover:text-ink'
              }`}
            >
              {label}
              <span className={`tabular-nums ${active ? 'text-white/80' : 'text-ink-3'}`}>{count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function AccountRow({
  account,
  isYou,
  onEdit,
  onReset,
}: {
  account: Account
  isYou: boolean
  onEdit: () => void
  onReset: () => void
}) {
  const attention = !account.isActive
    ? { label: 'Disabled', className: 'bg-danger-soft text-[#7a1a12]' }
    : account.mustChangePassword
      ? { label: 'Temporary password', className: 'bg-warn-soft text-warn-ink' }
      : null
  return (
    <li className="grid gap-x-6 gap-y-3 rounded-(--radius-panel) bg-surface px-5 py-4 md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)_auto] md:items-center">
      <div className="min-w-0">
        <p className={`truncate text-[15px] font-semibold ${account.isActive ? 'text-ink' : 'text-ink-2'}`}>
          {account.displayName}
          {isYou && <span className="font-normal text-ink-3"> (you)</span>}
        </p>
        <p className="truncate text-sm text-ink-3">{account.email}</p>
      </div>
      <div className="min-w-0 text-sm">
        <p className="flex flex-wrap items-center gap-2 text-ink-2">
          {rolesText(account)}
          {attention && (
            <span className={`rounded-(--radius-control) px-2 py-0.5 font-medium ${attention.className}`}>
              {attention.label}
            </span>
          )}
        </p>
        <p className="mt-0.5 text-ink-3">{accountStatus(account)}</p>
      </div>
      <div className="-ml-3 flex gap-1 md:ml-0">
        <Button variant="ghost" className="h-9 px-3" onClick={onEdit} aria-label={`Edit ${account.displayName}`}>
          Edit
        </Button>
        <Button
          variant="ghost"
          className="h-9 px-3"
          onClick={onReset}
          aria-label={`Reset password for ${account.displayName}`}
        >
          Reset password
        </Button>
      </div>
    </li>
  )
}

interface AccountFormDialogProps {
  auth: AuthApi
  /** Absent when creating. */
  account?: Account
  isYou?: boolean
  onClose: () => void
  onCreated?: (result: TemporaryPassword) => void
  onSaved?: (account: Account) => void
}

function AccountFormDialog({ auth, account, isYou = false, onClose, onCreated, onSaved }: AccountFormDialogProps) {
  const editing = account !== undefined
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState(account?.displayName ?? '')
  const [roles, setRoles] = useState<Role[]>(account?.roles ?? ['clinician'])
  const [isActive, setIsActive] = useState(account?.isActive ?? true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const firstField = useRef<HTMLInputElement>(null)
  const id = useId()

  const toggleRole = (role: Role, checked: boolean) =>
    setRoles((current) => ROLES.filter((r) => (r === role ? checked : current.includes(r))))

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!editing && !email.trim()) return setError('Enter an email address.')
    if (!displayName.trim()) return setError('Enter a name.')
    if (roles.length === 0) return setError('Choose at least one role.')
    setBusy(true)
    setError(null)
    try {
      if (editing) {
        onSaved?.(
          await auth.updateAccount(account.id, {
            displayName: displayName.trim(),
            roles,
            isActive,
          }),
        )
      } else {
        onCreated?.(await auth.createAccount({ email: email.trim(), displayName: displayName.trim(), roles }))
      }
    } catch (caught) {
      setError(errorMessage(caught, 'The account was not saved. Try again.'))
      setBusy(false)
    }
  }

  const rolesChanged = editing && roles.join() !== account.roles.join()

  return (
    <Dialog
      title={editing ? `Edit ${account.displayName}` : 'New account'}
      error={error}
      busy={busy}
      onClose={onClose}
      initialFocus={firstField}
      as={(content) => (
        <form noValidate onSubmit={handleSubmit} aria-label={editing ? 'Edit account' : 'New account'}>
          {content}
        </form>
      )}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            {editing ? 'Save changes' : 'Create account'}
          </Button>
        </>
      }
    >
      {editing ? (
        <p>
          Email <span className="font-medium text-ink">{account.email}</span>
        </p>
      ) : (
        <div>
          <label htmlFor={`${id}-email`} className={labelClass}>
            Email
          </label>
          <input
            ref={firstField}
            id={`${id}-email`}
            type="email"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={`mt-2 ${inputClass}`}
          />
        </div>
      )}
      <div>
        <label htmlFor={`${id}-name`} className={labelClass}>
          Name
        </label>
        <input
          ref={editing ? firstField : undefined}
          id={`${id}-name`}
          type="text"
          autoComplete="off"
          maxLength={100}
          placeholder="Dr Jane Doe"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className={`mt-2 ${inputClass}`}
        />
      </div>
      <fieldset>
        <legend className={labelClass}>Roles</legend>
        <div className="mt-2 space-y-2">
          {ROLES.map((role) => (
            <label
              key={role}
              className="flex cursor-pointer items-start gap-3 rounded-(--radius-control) bg-sunken px-3 py-3 has-checked:bg-accent-soft has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent"
            >
              <input
                type="checkbox"
                checked={roles.includes(role)}
                onChange={(e) => toggleRole(role, e.target.checked)}
                className="mt-1 size-4 shrink-0 accent-accent"
              />
              <span>
                <span className="block font-medium text-ink">{ROLE_LABELS[role]}</span>
                <span className="block text-sm text-ink-2">{ROLE_HINTS[role]}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {editing && (
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={isActive}
            disabled={isYou}
            onChange={(e) => setIsActive(e.target.checked)}
            className="mt-1 size-4 shrink-0 accent-accent"
          />
          <span>
            <span className="block font-medium text-ink">Can sign in</span>
            <span className="block text-sm text-ink-2">
              {isYou
                ? 'You cannot disable your own account.'
                : 'Turning this off signs the person out at once and blocks sign-in.'}
            </span>
          </span>
        </label>
      )}
      {rolesChanged && (
        <Alert tone="info">
          {isYou
            ? 'Changing your own roles signs you out. Sign in again to use the new roles.'
            : 'Changing roles signs this person out of every device.'}
        </Alert>
      )}
      {!editing && (
        <p className="text-sm text-ink-3">
          AirSOAP creates a temporary password for you to hand over. It must be changed at first sign-in.
        </p>
      )}
    </Dialog>
  )
}

function ResetPasswordDialog({
  auth,
  account,
  onClose,
  onReset,
}: {
  auth: AuthApi
  account: Account
  onClose: () => void
  onReset: (result: TemporaryPassword) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      onReset(await auth.resetPassword(account.id))
    } catch (caught) {
      setError(errorMessage(caught, 'The password was not reset. Try again.'))
      setBusy(false)
    }
  }
  return (
    <ConfirmDialog
      title={`Reset the password for ${account.displayName}?`}
      confirmLabel="Reset password"
      tone="danger"
      busy={busy}
      error={error}
      onConfirm={confirm}
      onCancel={onClose}
    >
      <p>
        Their current password stops working and they are signed out everywhere. You will get a temporary password to
        give them; they choose a new one when they sign in.
      </p>
    </ConfirmDialog>
  )
}

function TemporaryPasswordDialog({
  result,
  reason,
  onClose,
}: {
  result: TemporaryPassword
  reason: 'created' | 'reset'
  onClose: () => void
}) {
  const [copied, setCopied] = useState<'yes' | 'failed' | null>(null)
  const doneRef = useRef<HTMLButtonElement>(null)
  const canCopy = typeof navigator !== 'undefined' && Boolean(navigator.clipboard)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(result.temporaryPassword)
      setCopied('yes')
    } catch {
      setCopied('failed')
    }
  }

  return (
    <Dialog
      title={reason === 'created' ? 'Account created' : 'Password reset'}
      onClose={onClose}
      initialFocus={doneRef}
      footer={
        <>
          {canCopy && (
            <Button variant="secondary" onClick={copy}>
              {copied === 'yes' ? 'Copied' : 'Copy password'}
            </Button>
          )}
          <Button ref={doneRef} onClick={onClose}>
            Done
          </Button>
        </>
      }
    >
      <p>
        Temporary password for <span className="font-medium text-ink">{result.user.displayName}</span> (
        {result.user.email}):
      </p>
      <p
        aria-label="Temporary password"
        className="rounded-(--radius-control) bg-sunken px-4 py-3 font-mono text-lg tracking-wide break-all text-ink select-all"
      >
        {result.temporaryPassword}
      </p>
      {copied === 'failed' && <p className="text-sm text-danger">Copying is not available here. Select the text instead.</p>}
      <Alert tone="warning">
        This is the only time it is shown. Give it to them in person, not by email or chat. They must replace it when
        they first sign in.
      </Alert>
    </Dialog>
  )
}
