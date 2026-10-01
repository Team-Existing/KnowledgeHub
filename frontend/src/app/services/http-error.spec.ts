import { HttpErrorResponse } from '@angular/common/http'
import { errorMessage } from './http-error'

describe('errorMessage', () => {
  const http = (status: number, error: unknown) =>
    new HttpErrorResponse({ status, error, url: '/x', statusText: 'Err' })

  it('prefers the API detail string', () => {
    expect(errorMessage(http(415, { detail: 'Legacy .doc files are not supported.' }), 'Upload failed'))
      .toBe('Legacy .doc files are not supported.')
  })

  it('joins FastAPI validation errors', () => {
    const err = http(422, { detail: [{ msg: 'field required' }, { msg: 'too short' }] })
    expect(errorMessage(err, 'x')).toBe('field required; too short')
  })

  it('reports an unreachable server', () => {
    expect(errorMessage(http(0, null), 'x')).toBe('Cannot reach the server')
  })

  it('falls back when the API gives no detail', () => {
    expect(errorMessage(http(500, 'Internal Server Error'), 'Save failed')).toBe('Save failed')
  })

  it('uses Error messages and falls back for unknown values', () => {
    expect(errorMessage(new Error('Install request failed'), 'x')).toBe('Install request failed')
    expect(errorMessage('weird', 'fallback')).toBe('fallback')
  })
})
