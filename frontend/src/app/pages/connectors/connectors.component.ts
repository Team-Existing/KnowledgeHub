import { Component, OnDestroy, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { Connector, ConnectorField, ConnectorKind, SyncedDocument } from '../../models/api'

const POLL_MS = 3000

@Component({
  selector: 'app-connectors',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './connectors.component.html'
})
export class ConnectorsComponent implements OnInit, OnDestroy {
  kinds: ConnectorKind[] = []
  connectors: Connector[] = []
  error = ''; busyId = ''

  // add / edit form
  formOpen = false
  editingId: string | null = null
  formKind = ''
  formName = ''
  formValues: Record<string, string> = {}
  formError = ''; saving = false

  documents: Record<string, SyncedDocument[]> = {}
  private pollTimer: ReturnType<typeof setTimeout> | null = null

  constructor(private http: HttpClient) {}

  async ngOnInit() {
    try {
      this.kinds = await firstValueFrom(this.http.get<ConnectorKind[]>(`${API_BASE}/connectors/kinds`))
    } catch (e) { this.error = errorMessage(e, 'Could not load connector types') }
    await this.load()
  }

  ngOnDestroy() { if (this.pollTimer) clearTimeout(this.pollTimer) }

  async load() {
    try {
      this.connectors = await firstValueFrom(this.http.get<Connector[]>(`${API_BASE}/connectors`))
    } catch (e) { this.error = errorMessage(e, 'Could not load connectors') }
    // keep polling while any sync runs
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollTimer = this.connectors.some(c => c.last_status === 'running' || c.last_status === 'queued')
      ? setTimeout(() => this.load(), POLL_MS) : null
  }

  get kind(): ConnectorKind | undefined { return this.kinds.find(k => k.kind === this.formKind) }

  get blockedKinds(): ConnectorKind[] { return this.kinds.filter(k => !k.allowed && k.disabled_reason) }

  get hasSecrets(): boolean { return this.kind?.fields.some(f => f.secret) ?? false }

  openNew(kind: ConnectorKind) {
    this.editingId = null
    this.formKind = kind.kind
    this.formName = kind.label.split(' — ')[0]
    this.formValues = {}
    for (const f of kind.fields) this.formValues[f.name] = this.display(f, f.default)
    this.formError = ''; this.formOpen = true
  }

  openEdit(c: Connector) {
    const kind = this.kinds.find(k => k.kind === c.kind)
    if (!kind) return
    this.editingId = c.id
    this.formKind = c.kind
    this.formName = c.name
    this.formValues = {}
    for (const f of kind.fields) this.formValues[f.name] = this.display(f, c.config[f.name] ?? f.default)
    this.formError = ''; this.formOpen = true
  }

  private display(f: ConnectorField, value: unknown): string {
    if (value === null || value === undefined) return ''
    return f.type === 'list' && Array.isArray(value) ? value.join(', ') : String(value)
  }

  async save() {
    const kind = this.kind
    if (!kind) return
    this.saving = true; this.formError = ''
    const config: Record<string, unknown> = {}
    for (const f of kind.fields) config[f.name] = this.formValues[f.name] ?? ''
    try {
      if (this.editingId) {
        await firstValueFrom(this.http.patch(`${API_BASE}/connectors/${this.editingId}`, { name: this.formName, config }))
      } else {
        await firstValueFrom(this.http.post(`${API_BASE}/connectors`, { kind: kind.kind, name: this.formName, config }))
      }
      this.formOpen = false
      await this.load()
    } catch (e) { this.formError = errorMessage(e, 'Could not save the connector') }
    finally { this.saving = false }
  }

  async sync(c: Connector) {
    this.busyId = c.id; this.error = ''
    try {
      await firstValueFrom(this.http.post(`${API_BASE}/connectors/${c.id}/sync`, {}))
      delete this.documents[c.id]
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not start the sync') }
    finally { this.busyId = '' }
  }

  async remove(c: Connector) {
    if (!confirm(`Remove “${c.name}”?`)) return
    const alsoData = confirm('Also delete everything it imported? Cancel keeps the imported knowledge.')
    this.busyId = c.id; this.error = ''
    try {
      await firstValueFrom(this.http.delete(`${API_BASE}/connectors/${c.id}`, { params: { delete_artifacts: alsoData } }))
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not remove the connector') }
    finally { this.busyId = '' }
  }

  async toggleDocuments(c: Connector) {
    if (this.documents[c.id]) { delete this.documents[c.id]; return }
    try {
      this.documents[c.id] = await firstValueFrom(this.http.get<SyncedDocument[]>(`${API_BASE}/connectors/${c.id}/documents`))
    } catch (e) { this.error = errorMessage(e, 'Could not load synced documents') }
  }

  statusLabel(c: Connector): string {
    return ({ never: 'Never synced', queued: 'Queued…', running: 'Syncing…', ok: 'Synced', partial: 'Synced with errors',
              error: 'Sync failed', interrupted: 'Interrupted' } as Record<string, string>)[c.last_status] ?? c.last_status
  }

  statusClass(c: Connector): string {
    return ({ ok: 'status-active', partial: 'status-proposed', running: 'status-proposed', queued: 'status-proposed',
              error: 'status-rejected', interrupted: 'status-rejected' } as Record<string, string>)[c.last_status] ?? ''
  }

  /** The config value worth showing on the card: a path, repo, URL or query. */
  summary(c: Connector): string {
    const cfg = c.config
    return String(cfg['path'] ?? cfg['repo_url'] ?? cfg['base_url'] ?? cfg['api_url'] ?? '')
  }

  isExternal(url: string): boolean { return /^https?:\/\//.test(url) }
}
