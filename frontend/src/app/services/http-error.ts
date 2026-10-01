import { HttpErrorResponse } from '@angular/common/http'

/**
 * Human-readable message for a caught error. Prefers the API's own `detail`
 * (FastAPI) over Angular's generic "Http failure response for …" text.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof HttpErrorResponse) {
    const detail: unknown = err.error?.detail
    if (typeof detail === 'string' && detail) return detail
    // 422 validation errors: [{ loc, msg, type }, …]
    if (Array.isArray(detail) && detail.length) {
      return detail.map(d => (typeof d?.msg === 'string' ? d.msg : String(d))).join('; ')
    }
    if (err.status === 0) return 'Cannot reach the server'
    return fallback
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}
