import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { ActivatedRoute, RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { Connector, CopilotPreview, CopilotRun, CopilotSettings } from '../../models/api'

const KINDS = ['github', 'jira', 'linear']

/** What a pull request or issue is called on each platform, for the form */
export const TARGET: Record<string, { noun: string; label: string; placeholder: string }> = {
  github: { noun: 'pull request', label: 'Pull request number', placeholder: 'e.g. 42' },
  jira: { noun: 'issue', label: 'Issue key', placeholder: 'e.g. ARCH-12' },
  linear: { noun: 'issue', label: 'Issue identifier', placeholder: 'e.g. ENG-42' },
}

/**
 * The Decision Copilot for a GitHub, Jira or Linear connector: preview or post
 * the comment a pull request or issue gets, turn on the webhook, and see what
 * it did. Comments only.
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
  /** a secret Knowledge Hubs created, shown once */
  secret = ''
  /** Linear: the signing secret Linear shows, pasted here */
  linearSecret = ''
  ref = ''
  preview: CopilotPreview | null = null
  runs: CopilotRun[] = []
  error = ''; notice = ''; busy = false; copied = ''

  readonly target = TARGET

  constructor(private http: HttpClient, private route: ActivatedRoute) {}

  async ngOnInit() {
    try {
      this.connectors = (await firstValueFrom(this.http.get<Connector[]>(`${API_BASE}/connectors`)))
        .filter(c => KINDS.includes(c.kind))
    } catch (e) { this.error = errorMessage(e, 'Could not load connectors') }
    const wanted = this.route.snapshot.queryParamMap.get('connector')
    this.connectorId = this.connectors.find(c => c.id === wanted)?.id ?? this.connectors[0]?.id ?? ''
    await Promise.all([this.loadSettings(), this.loadRuns()])
  }

  get connector(): Connector | undefined { return this.connectors.find(c => c.id === this.connectorId) }
  get kind(): string { return this.connector?.kind ?? 'github' }

  where(c: Connector): string {
    const address = c.config['repo_url'] ?? c.config['base_url'] ?? 'Linear'
    return `${c.name} · ${address}`
  }

  async choose(id: string) {
    this.connectorId = id; this.preview = null; this.secret = ''; this.ref = ''
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
    if (rotate && !confirm('Create a new webhook secret? Webhooks stop being accepted until you paste the new one there.')) return
    const body: Record<string, unknown> = { enabled, rotate_secret: rotate }
    if (this.kind === 'linear' && this.linearSecret.trim()) body['webhook_secret'] = this.linearSecret.trim()
    await this.run(async () => {
      const s = await firstValueFrom(this.http.put<CopilotSettings>(
        `${API_BASE}/connectors/${encodeURIComponent(this.connectorId)}/copilot`, body))
      this.settings = s
      this.secret = s.webhook_secret ?? ''
      this.linearSecret = ''
    }, 'Could not change the setting')
  }

  async analyze() {
    await this.run(async () => {
      this.preview = await firstValueFrom(this.http.post<CopilotPreview>(`${API_BASE}/knowledge/copilot/preview`,
        { connector_id: this.connectorId, ref: this.ref.trim() }))
    }, `Could not analyze the ${TARGET[this.kind].noun}`)
  }

  async post() {
    await this.run(async () => {
      const r = await firstValueFrom(this.http.post<CopilotRun>(`${API_BASE}/knowledge/copilot/post`,
        { connector_id: this.connectorId, ref: this.ref.trim() }))
      const noun = TARGET[this.kind].noun
      this.notice = r.status === 'updated' ? `Updated the comment on the ${noun}.` : `Posted the comment on the ${noun}.`
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
