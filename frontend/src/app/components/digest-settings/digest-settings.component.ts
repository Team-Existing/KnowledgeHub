import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { DigestInfo, DigestList, DigestPreview } from '../../models/api'

/** The weekly digest of what needs attention, posted to Slack channels (backend services/digest.py). */
@Component({
  selector: 'app-digest-settings',
  imports: [CommonModule, FormsModule],
  templateUrl: './digest-settings.component.html'
})
export class DigestSettingsComponent implements OnInit {
  list: DigestList | null = null
  preview: DigestPreview | null = null
  name = ''; webhookUrl = ''; weekday = 0
  error = ''; notice = ''; busy = false

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.load() }

  async load() {
    try {
      const [list, preview] = await Promise.all([
        firstValueFrom(this.http.get<DigestList>(`${API_BASE}/knowledge/digests`)),
        firstValueFrom(this.http.get<DigestPreview>(`${API_BASE}/knowledge/digests/preview`)),
      ])
      this.list = list; this.preview = preview
    } catch (e) { this.error = errorMessage(e, 'Could not load digests') }
  }

  async add() {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/digests`,
        { name: this.name, webhook_url: this.webhookUrl, weekday: Number(this.weekday) }))
      this.name = ''; this.webhookUrl = ''
      await this.load()
    }, 'Could not add the digest')
  }

  async sendNow(d: DigestInfo) {
    await this.run(async () => {
      const r = await firstValueFrom(this.http.post<{ status: string; total: number }>(
        `${API_BASE}/knowledge/digests/${encodeURIComponent(d.id)}/send`, {}))
      this.notice = `Sent to ${d.name}: ${r.total} thing${r.total === 1 ? '' : 's'} to look at.`
      await this.load()
    }, 'Could not send')
  }

  async remove(d: DigestInfo) {
    if (!confirm(`Stop sending the digest to ${d.name}?`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/digests/${encodeURIComponent(d.id)}`))
      await this.load()
    }, 'Could not remove it')
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''; this.notice = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
