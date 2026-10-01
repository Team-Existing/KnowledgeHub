import { Injectable, signal, computed } from '@angular/core'
import { HttpClient, HttpParams } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { TokenResponse } from '../models/api'

export const API_BASE = ''

// The Authorization header is added by authInterceptor, not per call.
@Injectable({ providedIn: 'root' })
export class AuthService {
  private _token = signal<string | null>(localStorage.getItem('kh_token'))
  readonly token = this._token.asReadonly()
  readonly isLoggedIn = computed(() => !!this._token())

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
  }

  async register(username: string, password: string): Promise<void> {
    await firstValueFrom(this.http.post(`${API_BASE}/auth/register`, { username, password }))
  }

  async registerAndLogin(username: string, password: string): Promise<void> {
    await this.register(username, password)
    await this.login(username, password)
  }

  logout(): void {
    localStorage.removeItem('kh_token')
    this._token.set(null)
  }
}
