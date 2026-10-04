import { Component, OnInit } from '@angular/core'
import { CommonModule, DecimalPipe } from '@angular/common';
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE, AuthService } from '../../services/auth.service'
import { ReembedResult } from '../../models/api'
import { LocalModel, ModelService } from '../../services/model.service'
import { errorMessage } from '../../services/http-error'

/**
 * The three supported local models: download any of them, and choose which
 * one you use. The choice is saved on your account, so it carries over to
 * future sessions.
 */
@Component({
  selector: 'app-model-manager',
  imports: [CommonModule, DecimalPipe],
  templateUrl: './model-manager.component.html',
  styleUrl: './model-manager.component.css'
})
export class ModelManagerComponent implements OnInit {
  busyId: string | null = null
  errorId: string | null = null
  errorMsg = ''
  successMsg = ''
  reembedding = false
  reembedResult: ReembedResult | null = null
  reembedError = ''

  constructor(public service: ModelService, private http: HttpClient, public auth: AuthService) {}

  async ngOnInit() { await this.service.loadAll() }

  get models(): LocalModel[] { return this.service.localModels() }
  get installedCount(): number { return this.models.filter(m => m.installed).length }
  get activeModel(): LocalModel | undefined { return this.models.find(m => m.active) }
  get ramGb(): number { return this.service.systemInfo()?.ramGb ?? 0 }
  get recommended(): string | undefined { return this.service.systemInfo()?.recommendedTier }
  get ollamaReachable(): boolean { return this.service.modelStatus()?.llm.ollamaReachable !== false }

  tooBig(m: LocalModel): boolean { return this.ramGb > 0 && this.ramGb < m.minRamGb }

  private flash(message: string) {
    this.successMsg = message
    setTimeout(() => this.successMsg = '', 4000)
  }

  private async run(model: LocalModel, action: () => Promise<void>, done: string, failed: string) {
    this.busyId = model.id
    this.errorId = null
    this.errorMsg = ''
    try {
      await action()
      this.flash(done)
    } catch (e) {
      this.errorId = model.id
      this.errorMsg = errorMessage(e, failed)
    } finally {
      this.busyId = null
    }
  }

  install(model: LocalModel) {
    return this.run(model, () => this.service.installModel(model.id),
      `✓ ${model.name} downloaded`, 'Download failed. Is Ollama running?')
  }

  use(model: LocalModel) {
    return this.run(model, () => this.service.setActiveModel(model.id),
      `✓ Now using ${model.name}. This is saved for your future sessions.`, 'Could not switch model')
  }

  remove(model: LocalModel) {
    if (!confirm(`Remove ${model.name} from this computer? It will need to be downloaded again to use it.`)) return
    return this.run(model, () => this.service.removeModel(model.id), `✓ ${model.name} removed`, 'Remove failed')
  }

  async reembed() {
    this.reembedding = true
    this.reembedResult = null
    this.reembedError = ''
    try {
      this.reembedResult = await firstValueFrom(this.http.post<ReembedResult>(`${API_BASE}/knowledge/reembed`, {}))
    } catch (e) {
      this.reembedError = errorMessage(e, 'Re-embed failed')
    } finally {
      this.reembedding = false
    }
  }
}
