import { Injectable, signal, computed } from '@angular/core'
import { HttpClient, HttpParams } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { Me, Permission, SetupStatus, SsoProvider, TokenResponse } from '../models/api'

export const API_BASE = ''

// The Authorization header is added by authInterceptor, not per call.
@Injectable({ providedIn: 'root' })
export class AuthService {
  private _token = signal<string | null>(localStorage.getItem('kh_token'))
  readonly token = this._token.asReadonly()
  readonly isLoggedIn = computed(() => !!this._token())
  /** The signed-in user's role and permissions; null until loaded. Only drives the UI: the server enforces. */
  private _me = signal<Me | null>(null)
  readonly me = this._me.asReadonly()

  constructor(private http: HttpClient) {}

  async login(username: string, password: string): Promise<void> {
    const body = new HttpParams().set('username', username).set('password', password)
    const data = await firstValueFrom(
      this.http.post<TokenResponse>(`${API_BASE}/auth/token`, body.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      })
    )
    localStorage.setItem('kh_token', data.access_token)
    this._token.set(data.access_token)
    await this.loadMe()
  }

  /** A token from single sign-on (the /sso/complete page) */
  async useToken(token: string): Promise<void> {
    localStorage.setItem('kh_token', token)
    this._token.set(token)
    await this.loadMe()
  }

  /** The sign-in methods this server offers besides passwords (public) */
  ssoProviders(): Promise<SsoProvider[]> {
    return firstValueFrom(this.http.get<SsoProvider[]>(`${API_BASE}/auth/sso/providers`))
  }

  /** Where the browser goes to sign in with a provider; it comes back to `next` */
  ssoStartUrl(providerId: string, next: string | null): string {
    const query = next ? `?next=${encodeURIComponent(next)}` : ''
    return `${API_BASE}/auth/sso/${encodeURIComponent(providerId)}/start${query}`
  }

  async loadMe(): Promise<void> {
    if (!this._token()) { this._me.set(null); return }
    try {
      this._me.set(await firstValueFrom(this.http.get<Me>(`${API_BASE}/auth/me`)))
    } catch { this._me.set(null) }
  }

  can(permission: Permission): boolean {
    return this._me()?.permissions.includes(permission) ?? false
  }

  /** setupToken: only for the first (admin) account, when the server sets SETUP_TOKEN */
  async register(username: string, password: string, setupToken?: string): Promise<void> {
    const body = setupToken ? { username, password, setup_token: setupToken } : { username, password }
    await firstValueFrom(this.http.post(`${API_BASE}/auth/register`, body))
  }

  /** Whether the first account still needs the server's setup token. */
  async setupStatus(): Promise<SetupStatus> {
    return firstValueFrom(this.http.get<SetupStatus>(`${API_BASE}/auth/setup`))
  }

  async registerAndLogin(username: string, password: string, setupToken?: string): Promise<void> {
    await this.register(username, password, setupToken)
    await this.login(username, password)
  }

  /** Sign out on the server too: every token of the account stops working, so a copied one can't be reused. */
  async signOut(): Promise<void> {
    if (this._token()) {
      try { await firstValueFrom(this.http.post(`${API_BASE}/auth/logout`, null)) } catch { /* expired already */ }
    }
    this.logout()
  }

  /** Forget the token in this browser only. */
  logout(): void {
    localStorage.removeItem('kh_token')
    this._token.set(null)
    this._me.set(null)
  }
}
