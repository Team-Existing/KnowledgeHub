import { Injectable, signal, computed } from '@angular/core'
import { HttpClient, HttpParams } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'

export const API_BASE = ''

@Injectable({ providedIn: 'root' })
export class AuthService {
  private _token = signal<string | null>(localStorage.getItem('kh_token'))
  readonly token = this._token.asReadonly()
  readonly isLoggedIn = computed(() => !!this._token())

  constructor(private http: HttpClient) {}

  authHeaders(): Record<string, string> {
    const t = this._token()
    return t ? { Authorization: `Bearer ${t}` } : {}
  }

  async login(username: string, password: string): Promise<void> {
    const body = new HttpParams().set('username', username).set('password', password)
    const data: any = await firstValueFrom(
      this.http.post(`${API_BASE}/auth/token`, body.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      })
    )
    localStorage.setItem('kh_token', data.access_token)
    this._token.set(data.access_token)
  }

  async register(username: string, password: string, workspace_id?: string): Promise<void> {
    await firstValueFrom(
      this.http.post(`${API_BASE}/auth/register`, { username, password, workspace_id: workspace_id || username })
    )
  }

  async registerAndLogin(username: string, password: string): Promise<void> {
    await this.register(username, password)
    await this.login(username, password)
  }

  logout(): void {
    localStorage.removeItem('kh_token')
    this._token.set(null)
  }

  async getWorkspaceSettings(): Promise<any> {
    return firstValueFrom(this.http.get(`${API_BASE}/workspace/settings`, { headers: this.authHeaders() }))
  }

  async updateWorkspaceSettings(settings: any): Promise<any> {
    return firstValueFrom(this.http.patch(`${API_BASE}/workspace/settings`, settings, { headers: this.authHeaders() }))
  }

  async listProviderConfigs(): Promise<any[]> {
    return firstValueFrom(this.http.get<any[]>(`${API_BASE}/workspace/provider-configs`, { headers: this.authHeaders() }))
  }

  async createProviderConfig(body: any): Promise<any> {
    return firstValueFrom(this.http.post(`${API_BASE}/workspace/provider-configs`, body, { headers: this.authHeaders() }))
  }

  async updateProviderConfig(id: string, body: any): Promise<any> {
    return firstValueFrom(this.http.put(`${API_BASE}/workspace/provider-configs/${id}`, body, { headers: this.authHeaders() }))
  }

  async deleteProviderConfig(id: string): Promise<void> {
    await firstValueFrom(this.http.delete(`${API_BASE}/workspace/provider-configs/${id}`, { headers: this.authHeaders() }))
  }
}
