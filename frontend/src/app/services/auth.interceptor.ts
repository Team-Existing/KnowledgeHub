import { inject } from '@angular/core'
import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http'
import { Router } from '@angular/router'
import { catchError, throwError } from 'rxjs'
import { API_BASE, AuthService } from './auth.service'
import { SpaceService } from './space.service'

/** True for requests to our own API — never leak the token to other hosts. */
export function isApiRequest(url: string): boolean {
  return API_BASE ? url.startsWith(API_BASE) : !/^[a-z][a-z\d+.-]*:\/\//i.test(url)
}

// Wrong credentials on these return 401 too — that is not an expired session.
const AUTH_ENDPOINTS = [`${API_BASE}/auth/token`, `${API_BASE}/auth/register`]

/**
 * Adds `Authorization: Bearer <token>` to API requests, and signs the user out
 * when the API rejects the token (expired or revoked), so every page doesn't
 * have to handle a 401 itself.
 *
 * Only covers HttpClient; the streaming model install uses fetch() and sets
 * the header itself (ModelService.installModel).
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!isApiRequest(req.url)) return next(req)

  const auth = inject(AuthService)
  const router = inject(Router)
  const spaces = inject(SpaceService)
  const token = auth.token()
  const headers: Record<string, string> = {}
  if (token && !req.headers.has('Authorization')) headers['Authorization'] = `Bearer ${token}`
  // which space (personal or group) the request acts in; see SpaceService
  const space = spaces.activeId()
  if (token && space && !req.headers.has('X-Space')) headers['X-Space'] = space
  const authed = Object.keys(headers).length ? req.clone({ setHeaders: headers }) : req

  return next(authed).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && token &&
          !AUTH_ENDPOINTS.some(path => req.url.startsWith(path))) {
        auth.logout()
        spaces.reset()
        router.navigate(['/login'], { queryParams: { expired: 1 } })
      } else if (err instanceof HttpErrorResponse && err.status === 403 &&
                 err.error?.detail === 'You are not a member of that group') {
        // removed from the active group (or it was deleted): go back to the personal space
        spaces.fallBackToPersonal()
      }
      return throwError(() => err)
    }),
  )
}
