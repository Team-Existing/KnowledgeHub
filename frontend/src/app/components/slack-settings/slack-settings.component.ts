import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { SlackIntegration } from '../../models/api'

/** Slack's /decisions command for this space: add the Slack app's signing secret, get the request URL. */
@Component({
  selector: 'app-slack-settings',
  imports: [CommonModule, FormsModule],
  templateUrl: './slack-settings.component.html'
})
export class SlackSettingsComponent implements OnInit {
  apps: SlackIntegration[] = []
  name = ''; secret = ''
  replacing: string | null = null; newSecret = ''
  error = ''; busy = false; copied = ''

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.load() }

  async load() {
    try { this.apps = await firstValueFrom(this.http.get<SlackIntegration[]>(`${API_BASE}/knowledge/slack`)) }
    catch (e) { this.error = errorMessage(e, 'Could not load Slack apps') }
  }

  async add() {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/slack`, { name: this.name, signing_secret: this.secret }))
      this.name = ''; this.secret = ''
      await this.load()
    }, 'Could not add the Slack app')
  }

  async replace(app: SlackIntegration) {
    await this.run(async () => {
      await firstValueFrom(this.http.put(`${API_BASE}/knowledge/slack/${encodeURIComponent(app.id)}`, { signing_secret: this.newSecret }))
      this.replacing = null; this.newSecret = ''
    }, 'Could not replace the secret')
  }

  async remove(app: SlackIntegration) {
    if (!confirm(`Remove ${app.name}? Its /decisions command stops working.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/slack/${encodeURIComponent(app.id)}`))
      await this.load()
    }, 'Could not remove it')
  }

  async copy(text: string, what: string) {
    try { await navigator.clipboard.writeText(text); this.copied = what } catch { this.copied = '' }
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
