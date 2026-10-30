import { safeReturnUrl } from './auth.guard'

describe('safeReturnUrl', () => {
  it('keeps paths inside the app', () => {
    expect(safeReturnUrl('/join/abc123')).toBe('/join/abc123')
    expect(safeReturnUrl('/knowledge?tab=2')).toBe('/knowledge?tab=2')
  })

  it('refuses anything that would leave the app', () => {
    for (const url of ['https://evil.example', '//evil.example', '/\\evil.example', 'join/abc', '', null, undefined]) {
      expect(safeReturnUrl(url)).toBeNull()
    }
  })
})
