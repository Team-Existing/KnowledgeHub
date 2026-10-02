/**
 * Mirrors backend/app/usernames.py so the form can explain the rules before
 * submitting. The server is still the authority: it also rejects names that
 * differ from an existing one only in letter case ("Alice" vs "alice").
 */
export const USERNAME_RULES = '3 to 60 characters: letters, numbers, dot, underscore or hyphen. Not case-sensitive.'

const PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$/

export function isValidUsername(name: string): boolean {
  return PATTERN.test(name.normalize('NFKC').trim())
}
