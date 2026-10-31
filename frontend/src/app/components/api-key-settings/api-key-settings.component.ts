import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { ApiKeyInfo, CreatedApiKey } from '../../models/api'

/** Read-only API keys for this space, for developer portals and other tools (backend app/api_keys.py). */
@Component({
  selector: 'app-api-key-settings',
  imports: [CommonModule, FormsModule],
  templateUrl: './api-key-settings.component.html'
})
export class ApiKeySettingsComponent implements OnInit {
  keys: ApiKeyInfo[] = []
  name = ''
  /** shown once, right after it's created */
  created: CreatedApiKey | null = null
  error = ''; busy = false; copied = false

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.load() }

  async load() {
    try { this.keys = await firstValueFrom(this.http.get<ApiKeyInfo[]>(`${API_BASE}/knowledge/api-keys`)) }
    catch (e) { this.error = errorMessage(e, 'Could not load API keys') }
  }

  async create() {
    await this.run(async () => {
      this.created = await firstValueFrom(this.http.post<CreatedApiKey>(`${API_BASE}/knowledge/api-keys`, { name: this.name }))
      this.name = ''; this.copied = false
      await this.load()
    }, 'Could not create the key')
  }

  async revoke(k: ApiKeyInfo) {
    if (!confirm(`Revoke the key for ${k.name}? Whatever uses it stops working.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/api-keys/${encodeURIComponent(k.id)}`))
      if (this.created?.id === k.id) this.created = null
      await this.load()
    }, 'Could not revoke it')
  }

  async copy() {
    try { await navigator.clipboard.writeText(this.created?.key ?? ''); this.copied = true } catch { this.copied = false }
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
