export const MIN_PASSWORD_LENGTH = 12
export const MAX_PASSWORD_LENGTH = 128

/** The rules the backend enforces (backend/airsoap/auth/passwords.py), checked first to save a round trip. */
export function passwordProblem(password: string, email: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters.`
  if (password.length > MAX_PASSWORD_LENGTH) return `Use at most ${MAX_PASSWORD_LENGTH} characters.`
  const lowered = password.trim().toLowerCase()
  const address = email.toLowerCase()
  if (lowered === address || lowered === address.split('@')[0]) return 'The password must not be your email address.'
  return null
}
