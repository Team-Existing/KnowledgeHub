import { inject } from '@angular/core'
import { Router } from '@angular/router'
import { AuthService } from './auth.service'

export const authGuard = () => {
  const auth = inject(AuthService)
  const router = inject(Router)
  if (auth.isLoggedIn()) return true
  return router.createUrlTree(['/login'])
}

/** Pages only server admins use (the API refuses everyone else anyway): others go to the hub. */
export const adminGuard = async () => {
  const auth = inject(AuthService)
  const router = inject(Router)
  if (!auth.isLoggedIn()) return router.createUrlTree(['/login'])
  if (!auth.me()) await auth.loadMe()        // a direct visit: the role isn't loaded yet
  return auth.can('manage_users') ? true : router.createUrlTree(['/knowledge'])
}
