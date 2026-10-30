import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { ActivatedRoute } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE, AuthService } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { SsoAccount, SsoProvider } from '../../models/api'

/** Your account: its verified email and the single sign-on providers connected to it. */
@Component({
  selector: 'app-account',
  imports: [CommonModule],
  templateUrl: './account.component.html'
})
export class AccountComponent implements OnInit {
  account: SsoAccount | null = null
  providers: SsoProvider[] = []
  error = ''; notice = ''; busy = false

  constructor(private http: HttpClient, public auth: AuthService, route: ActivatedRoute) {
    const connected = route.snapshot.queryParamMap.get('connected')
    if (connected) this.notice = `Connected ${connected}. You can now sign in with it.`
  }

  async ngOnInit() {
    await this.load()
    this.providers = await this.auth.ssoProviders().catch(() => [])
  }

  async load() {
    try {
      this.account = await firstValueFrom(this.http.get<SsoAccount>(`${API_BASE}/auth/sso/identities`))
      await this.auth.loadMe()          // the email may have just been verified
    } catch (e) { this.error = errorMessage(e, 'Could not load your account') }
  }

  connected(p: SsoProvider): boolean { return !!this.account?.identities.some(i => i.provider === p.id) }

  /** The only way left to sign in can't be disconnected */
  canDisconnect(): boolean { return !!this.account && (this.account.has_password || this.account.identities.length > 1) }

  async connect(p: SsoProvider) {
    this.busy = true; this.error = ''
    try {
      const { url } = await firstValueFrom(this.http.post<{ url: string }>(
        `${API_BASE}/auth/sso/${encodeURIComponent(p.id)}/link`, {}))
      location.assign(url)                      // to the provider and back to this page
    } catch (e) { this.error = errorMessage(e, 'Could not start connecting'); this.busy = false }
  }

  async disconnect(provider: string, name: string) {
    if (!confirm(`Disconnect ${name}? You won't be able to sign in with it.`)) return
    this.busy = true; this.error = ''; this.notice = ''
    try {
      await firstValueFrom(this.http.delete(`${API_BASE}/auth/sso/identities/${encodeURIComponent(provider)}`))
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not disconnect') }
    finally { this.busy = false }
  }
}
