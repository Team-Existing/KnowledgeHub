import { Component } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { AuthService, API_BASE } from '../../services/auth.service'

interface ProviderConfig {
  id: string
  provider_type: string
  provider_name: string
  model_name?: string
  config_json?: any
  api_key_ref?: string
  is_active: boolean
  created_at: string
}

@Component({
  selector: 'app-workspace-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page-shell">
      <h2>Workspace Settings</h2>

      <div class="section-card">
        <h3>Provider policy</h3>
        <label>
          <input type="checkbox" [(ngModel)]="settings.allow_cloud_providers" /> Allow cloud providers
        </label>
        <div class="form-row">
          <label>Default LLM provider</label>
          <input [(ngModel)]="settings.default_llm_provider" placeholder="ollama or openai" />
        </div>
        <div class="form-row">
          <label>Default embedding provider</label>
          <input [(ngModel)]="settings.default_embedding_provider" placeholder="local or openai" />
        </div>
        <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;margin-top:0.5rem">
          <button class="primary" (click)="save()" [disabled]="saving || reembedding">
            {{ saving ? 'Saving…' : 'Save settings' }}
          </button>
          <button (click)="reembed()" [disabled]="reembedding || saving"
            title="Re-vectorise all items under the current embedding provider">
            {{ reembedding ? reembedStatus : 'Re-embed workspace' }}
          </button>
        </div>
        @if (reembedResult) {
          <p style="font-size:0.85rem;color:#475467;margin-top:0.5rem">
            Re-embedded {{ reembedResult.items_reembedded }} items +
            {{ reembedResult.summaries_reembedded }} summaries
            using {{ reembedResult.provider }} (dim={{ reembedResult.dimensions }})
          </p>
        }
        @if (reembedNote) {
          <p style="font-size:0.82rem;color:#667085;margin-top:0.35rem">{{ reembedNote }}</p>
        }
      </div>

      <div class="section-card">
        <h3>Provider configurations</h3>
        <div *ngIf="configs.length === 0" style="color:#667085">No workspace provider configs yet.</div>

        <div *ngFor="let cfg of configs" class="config-card">
          <ng-container *ngIf="editingConfigId !== cfg.id">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">
              <div>
                <strong>{{ cfg.provider_type }} / {{ cfg.provider_name }}</strong>
                <div style="font-size:0.85rem;color:#475569">Model: {{ cfg.model_name || 'default' }}</div>
                <div style="font-size:0.85rem;color:#475569">Active: {{ cfg.is_active }}</div>
              </div>
              <div style="display:flex;gap:0.4rem;flex-shrink:0">
                <button (click)="startEditConfig(cfg)">Edit</button>
                <button (click)="removeConfig(cfg.id)">Delete</button>
              </div>
            </div>
          </ng-container>

          <ng-container *ngIf="editingConfigId === cfg.id">
            <div class="form-row"><label>Type</label><input [(ngModel)]="editConfig.provider_type" /></div>
            <div class="form-row"><label>Name</label><input [(ngModel)]="editConfig.provider_name" /></div>
            <div class="form-row"><label>Model</label><input [(ngModel)]="editConfig.model_name" /></div>
            <div class="form-row"><label>API Key Ref</label><input [(ngModel)]="editConfig.api_key_ref" /></div>
            <div class="form-row"><label><input type="checkbox" [(ngModel)]="editConfig.is_active" /> Active</label></div>
            <div style="display:flex;gap:0.5rem;margin-top:0.5rem">
              <button class="primary" (click)="saveEditConfig(cfg.id)" [disabled]="updatingConfig">
                {{ updatingConfig ? 'Saving…' : 'Save' }}
              </button>
              <button (click)="editingConfigId = null">Cancel</button>
            </div>
          </ng-container>
        </div>

        <div style="margin-top:1rem;padding-top:1rem;border-top:1px solid #e5ecea">
          <h4 style="font-size:0.9rem;margin:0 0 0.75rem">Add configuration</h4>
          <div class="form-row"><label>Type</label><input [(ngModel)]="newConfig.provider_type" placeholder="llm or embedding" /></div>
          <div class="form-row"><label>Name</label><input [(ngModel)]="newConfig.provider_name" placeholder="ollama, openai, local" /></div>
          <div class="form-row"><label>Model</label><input [(ngModel)]="newConfig.model_name" placeholder="e.g. gpt-4o-mini" /></div>
          <div class="form-row"><label>API Key Ref</label><input [(ngModel)]="newConfig.api_key_ref" placeholder="optional secret ref" /></div>
          <div class="form-row"><label><input type="checkbox" [(ngModel)]="newConfig.is_active" /> Active</label></div>
          <button style="margin-top:0.5rem" (click)="addConfig()" [disabled]="creating">
            {{ creating ? 'Creating…' : 'Create config' }}
          </button>
        </div>
      </div>

      <div *ngIf="error" class="error-text">{{ error }}</div>
    </div>
  `,
  styles: [`
    .page-shell { max-width: 840px; margin: 2rem auto; padding: 0 1rem }
    .section-card { background:#fff;border:1px solid #dfe7e5;border-radius:8px;padding:1.25rem;margin-bottom:1.25rem }
    .section-card h3 { margin:0 0 1rem;font-size:1rem }
    .form-row { display:flex;flex-direction:column;gap:0.25rem;margin:0.75rem 0 }
    .form-row label { font-size:0.85rem;color:#475467;font-weight:500 }
    .config-card { background:#f8fbfa;border:1px solid #e5ecea;border-radius:6px;padding:0.75rem 1rem;margin-bottom:0.5rem }
  `]
})
export class WorkspaceSettingsComponent {
  settings = { allow_cloud_providers: false, default_llm_provider: 'ollama', default_embedding_provider: 'local' }
  private _savedEmbeddingProvider = 'local'

  configs: ProviderConfig[] = []
  newConfig: Partial<ProviderConfig> = { provider_type: '', provider_name: '', model_name: '', api_key_ref: '', is_active: true }

  editingConfigId: string | null = null
  editConfig: Partial<ProviderConfig> = {}

  saving = false; creating = false; updatingConfig = false; reembedding = false
  reembedStatus = ''; reembedResult: any = null; reembedNote = ''; error = ''

  constructor(private auth: AuthService, private http: HttpClient) {
    this.load()
  }

  async load() {
    try {
      const data: any = await this.auth.getWorkspaceSettings()
      this.settings = {
        allow_cloud_providers: data.allow_cloud_providers,
        default_llm_provider: data.default_llm_provider,
        default_embedding_provider: data.default_embedding_provider,
      }
      this._savedEmbeddingProvider = data.default_embedding_provider
      this.configs = await this.auth.listProviderConfigs()
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Unable to load workspace settings.'
    }
  }

  async save() {
    this.saving = true; this.error = ''; this.reembedNote = ''
    const embeddingChanged = this.settings.default_embedding_provider !== this._savedEmbeddingProvider
    try {
      await this.auth.updateWorkspaceSettings(this.settings)
      this._savedEmbeddingProvider = this.settings.default_embedding_provider
      await this.load()
      if (embeddingChanged) {
        this.reembedNote = 'Embedding provider changed — starting re-embed…'
        await this.reembed()
      }
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Unable to save workspace settings.'
    } finally { this.saving = false }
  }

  async reembed() {
    this.reembedding = true; this.reembedResult = null; this.error = ''
    const stages = ['Preparing…', 'Embedding items…', 'Embedding summaries…', 'Finalising…']
    let i = 0
    this.reembedStatus = stages[0]
    const interval = setInterval(() => { this.reembedStatus = stages[Math.min(++i, stages.length - 1)] }, 3000)
    try {
      this.reembedResult = await firstValueFrom(
        this.http.post(`${API_BASE}/knowledge/reembed`, {}, { headers: this.auth.authHeaders() })
      )
      this.reembedNote = ''
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Re-embed failed.'
    } finally {
      clearInterval(interval)
      this.reembedding = false
      this.reembedStatus = ''
    }
  }

  startEditConfig(cfg: ProviderConfig) {
    this.editingConfigId = cfg.id
    this.editConfig = {
      provider_type: cfg.provider_type,
      provider_name: cfg.provider_name,
      model_name: cfg.model_name ?? '',
      api_key_ref: cfg.api_key_ref ?? '',
      is_active: cfg.is_active,
    }
  }

  async saveEditConfig(id: string) {
    this.updatingConfig = true; this.error = ''
    try {
      await this.auth.updateProviderConfig(id, {
        provider_type: this.editConfig.provider_type,
        provider_name: this.editConfig.provider_name,
        model_name: this.editConfig.model_name,
        config_json: {},
        api_key_ref: this.editConfig.api_key_ref,
        is_active: this.editConfig.is_active,
      })
      this.editingConfigId = null
      await this.load()
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Unable to update provider config.'
    } finally { this.updatingConfig = false }
  }

  async addConfig() {
    this.creating = true; this.error = ''
    try {
      await this.auth.createProviderConfig({
        provider_type: this.newConfig.provider_type,
        provider_name: this.newConfig.provider_name,
        model_name: this.newConfig.model_name,
        config_json: {},
        api_key_ref: this.newConfig.api_key_ref,
        is_active: this.newConfig.is_active,
      })
      this.newConfig = { provider_type: '', provider_name: '', model_name: '', api_key_ref: '', is_active: true }
      await this.load()
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Unable to create provider config.'
    } finally { this.creating = false }
  }

  async removeConfig(id: string) {
    try {
      await this.auth.deleteProviderConfig(id)
      this.configs = this.configs.filter(c => c.id !== id)
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Unable to delete provider config.'
    }
  }
}
