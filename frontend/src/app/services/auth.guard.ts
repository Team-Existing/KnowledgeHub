import { inject } from '@angular/core'
import { Router, RouterStateSnapshot } from '@angular/router'
import { AuthService } from './auth.service'

/** Signed out: sign in first, then come back here (an invite link, for example). */
export const authGuard = (_route?: unknown, state?: RouterStateSnapshot) => {
  const auth = inject(AuthService)
  const router = inject(Router)
  if (auth.isLoggedIn()) return true
  const url = state?.url
  return router.createUrlTree(['/login'], url && url !== '/' ? { queryParams: { returnUrl: url } } : {})
}

/** Only paths inside the app: "//evil.example" or "https://..." would leave it after sign-in. */
export function safeReturnUrl(url: string | null | undefined): string | null {
  return url && url.startsWith('/') && !url.startsWith('//') && !url.includes('\\') ? url : null
}

/** Pages only server admins use (the API refuses everyone else anyway): others go to the hub. */
export const adminGuard = async () => {
  const auth = inject(AuthService)
  const router = inject(Router)
  if (!auth.isLoggedIn()) return router.createUrlTree(['/login'])
  if (!auth.me()) await auth.loadMe()        // a direct visit: the role isn't loaded yet
  return auth.can('manage_users') ? true : router.createUrlTree(['/knowledge'])
}
