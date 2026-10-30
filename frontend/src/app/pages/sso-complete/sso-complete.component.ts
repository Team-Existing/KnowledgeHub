import { Component, OnInit } from '@angular/core'
import { Router, RouterLink } from '@angular/router'
import { AuthService } from '../../services/auth.service'
import { safeReturnUrl } from '../../services/auth.guard'

/**
 * Where single sign-on ends (routers/sso.py): the session token arrives in the
 * URL fragment, which browsers never send to a server. It's stored, removed from
 * the address bar and history, and the user goes on to the page they started from.
 */
@Component({
  selector: 'app-sso-complete',
  imports: [RouterLink],
  template: `
    <div class="auth-shell"><div class="auth-card">
      @if (error) {
        <p class="error-text">{{ error }}</p>
        <a routerLink="/login">Back to sign-in</a>
      } @else {
        <p class="muted-text">Signing you in…</p>
      }
    </div></div>`,
})
export class SsoCompleteComponent implements OnInit {
  error = ''

  constructor(private auth: AuthService, private router: Router) {}

  async ngOnInit() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ''))
    const token = params.get('token')
    history.replaceState(null, '', location.pathname)        // the token mustn't linger in the address bar
    if (!token) { this.error = "The sign-in didn't complete. Try again."; return }
    await this.auth.useToken(token)
    if (!this.auth.me()) { this.error = "The sign-in didn't complete. Try again."; return }
    await this.router.navigateByUrl(safeReturnUrl(params.get('next')) ?? '/knowledge', { replaceUrl: true })
  }
}
