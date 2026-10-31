import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { ActivatedRoute, RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { Connector, CopilotPreview, CopilotRun, CopilotSettings } from '../../models/api'

/**
 * The Decision Copilot for a GitHub connector: preview or post the comment a
 * pull request gets, turn on the webhook, and see what it did. Comments only.
 */
@Component({
  selector: 'app-copilot',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './copilot.component.html'
})
export class CopilotComponent implements OnInit {
  connectors: Connector[] = []
  connectorId = ''
  settings: CopilotSettings | null = null
  /** shown once, right after it's created */
  secret = ''
  prNumber: number | null = null
  preview: CopilotPreview | null = null
  runs: CopilotRun[] = []
  error = ''; notice = ''; busy = false; copied = ''

  constructor(private http: HttpClient, private route: ActivatedRoute) {}

  async ngOnInit() {
    try {
      this.connectors = (await firstValueFrom(this.http.get<Connector[]>(`${API_BASE}/connectors`)))
        .filter(c => c.kind === 'github')
    } catch (e) { this.error = errorMessage(e, 'Could not load connectors') }
    const wanted = this.route.snapshot.queryParamMap.get('connector')
    this.connectorId = this.connectors.find(c => c.id === wanted)?.id ?? this.connectors[0]?.id ?? ''
    await Promise.all([this.loadSettings(), this.loadRuns()])
  }

  get connector(): Connector | undefined { return this.connectors.find(c => c.id === this.connectorId) }

  async choose(id: string) {
    this.connectorId = id; this.preview = null; this.secret = ''
    await this.loadSettings()
  }

  async loadSettings() {
    if (!this.connectorId) { this.settings = null; return }
    try {
      this.settings = await firstValueFrom(this.http.get<CopilotSettings>(
        `${API_BASE}/connectors/${encodeURIComponent(this.connectorId)}/copilot`))
    } catch (e) { this.error = errorMessage(e, 'Could not load the settings') }
  }

  async loadRuns() {
    try { this.runs = await firstValueFrom(this.http.get<CopilotRun[]>(`${API_BASE}/knowledge/copilot/runs`)) }
    catch { /* shown empty */ }
  }

  async setEnabled(enabled: boolean, rotate = false) {
    if (rotate && !confirm('Create a new webhook secret? GitHub stops being accepted until you paste the new one there.')) return
    await this.run(async () => {
      const s = await firstValueFrom(this.http.put<CopilotSettings>(
        `${API_BASE}/connectors/${encodeURIComponent(this.connectorId)}/copilot`, { enabled, rotate_secret: rotate }))
      this.settings = s
      this.secret = s.webhook_secret ?? ''
    }, 'Could not change the setting')
  }

  async analyze() {
    await this.run(async () => {
      this.preview = await firstValueFrom(this.http.post<CopilotPreview>(`${API_BASE}/knowledge/copilot/preview`,
        { connector_id: this.connectorId, pr_number: this.prNumber }))
    }, 'Could not analyze the pull request')
  }

  async post() {
    await this.run(async () => {
      const r = await firstValueFrom(this.http.post<CopilotRun>(`${API_BASE}/knowledge/copilot/post`,
        { connector_id: this.connectorId, pr_number: this.prNumber }))
      this.notice = r.status === 'updated' ? 'Updated the comment on the pull request.' : 'Posted the comment on the pull request.'
      await this.loadRuns()
    }, 'Could not post the comment')
  }

  async copy(text: string, what: string) {
    try { await navigator.clipboard.writeText(text); this.copied = what } catch { this.copied = '' }
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''; this.notice = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
