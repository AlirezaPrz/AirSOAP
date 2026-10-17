import type { Account } from '../api/auth'

export type Filter = 'all' | 'active' | 'disabled' | 'temporary'

export const FILTERS: { key: Filter; label: string; test: (a: Account) => boolean }[] = [
  { key: 'all', label: 'All', test: () => true },
  { key: 'active', label: 'Active', test: (a) => a.isActive },
  { key: 'disabled', label: 'Disabled', test: (a) => !a.isActive },
  { key: 'temporary', label: 'Temporary password', test: (a) => a.isActive && a.mustChangePassword },
]
