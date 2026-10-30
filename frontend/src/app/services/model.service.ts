import { Injectable, signal, computed } from '@angular/core'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { AuthService, API_BASE } from './auth.service'

/**
 * A model users can choose: one of the three local models (served by Ollama),
 * or a model of the server's OpenAI-compatible API (id "api/<name>").
 */
export interface LocalModel {
  id: string
  name: string
  size: string
  ramRequired: string
  minRamGb: number
  description: string
  provider: 'ollama' | 'api'
  /** "Ollama", or the API's name, e.g. "OpenAI" */
  providerName: string
  /** false: the text it works on is sent to providerName */
  local: boolean
  installed: boolean
  active: boolean
}

/** GET /models/catalog — public, used before sign-in. */
export interface ModelCatalog {
  /** false: this server has no local models, only API ones */
  ollamaEnabled: boolean
  ollamaReachable: boolean
  /** any model usable: an installed local one, or an API model */
  anyInstalled: boolean
  ramGb: number
  recommended: string
  models: LocalModel[]
}

export interface ModelStatus {
  llm: {
    provider: 'local' | 'api'
    local?: boolean
    providerName?: string
    model?: string | null
    installed?: boolean
    ollamaReachable?: boolean
  }
  embedding: {
    provider: 'local'
    model?: string
    installed?: boolean
  }
}

export interface SystemInfo {
  ramGb: number
  recommendedTier: string
}

export interface InstallProgress {
  modelId: string
  status: string
  completed: number
  total: number
  percent: number
}

/** "api/gpt-4.1-mini" → "gpt-4.1-mini"; local tags are unchanged */
export function modelName(id: string | null | undefined): string {
  return (id ?? '').replace(/^api\//, '')
}

/** Names of the services that hosted models send text to, e.g. ["OpenAI"] */
export function hostedProviders(models: LocalModel[]): string[] {
  return [...new Set(models.filter(m => !m.local).map(m => m.providerName))]
}

@Injectable({ providedIn: 'root' })
export class ModelService {
  private _modelStatus = signal<ModelStatus | null>(null)
  private _localModels = signal<LocalModel[]>([])
  private _systemInfo = signal<SystemInfo | null>(null)
  private _initialized = signal<boolean>(false)
  private _installProgress = signal<InstallProgress | null>(null)

  readonly modelStatus = this._modelStatus.asReadonly()
  readonly localModels = this._localModels.asReadonly()
  readonly systemInfo = this._systemInfo.asReadonly()
  readonly initialized = this._initialized.asReadonly()
  readonly installProgress = this._installProgress.asReadonly()

  readonly currentLlmProvider = computed(() => this._modelStatus()?.llm.provider ?? 'not-configured')

  /** Whether the signed-in user's model runs locally (true until the status is known) */
  readonly isLocal = computed(() => this._modelStatus()?.llm.local !== false)

  readonly currentLlmModel = computed(() => this._modelStatus()?.llm.model || 'Not configured')

  readonly statusBadgeColor = computed(() => this._modelStatus()?.llm.installed ? 'green' : 'red')

  readonly statusBadgeText = computed(() => {
    const llm = this._modelStatus()?.llm
    if (!llm?.installed) return 'No model active'
    return llm.local === false ? `${llm.providerName} · ${modelName(llm.model)}` : `Local · ${llm.model}`
  })

  constructor(private http: HttpClient, private auth: AuthService) {}

  /** Which of the three models are installed. Works signed out (login screen). */
  getCatalog(): Promise<ModelCatalog> {
    return firstValueFrom(this.http.get<ModelCatalog>(`${API_BASE}/models/catalog`))
  }

  async loadAll(): Promise<void> {
    this._initialized.set(false)
    await Promise.all([this.loadLocalModels(), this.loadModelStatus(), this.loadSystemInfo()])
    this._initialized.set(true)
  }

  async loadModelStatus(): Promise<void> {
    try {
      this._modelStatus.set(await firstValueFrom(this.http.get<ModelStatus>(`${API_BASE}/models/status`)))
    } catch (error) {
      console.error('Failed to load model status:', error)
    }
  }

  async loadSystemInfo(): Promise<void> {
    try {
      this._systemInfo.set(await firstValueFrom(this.http.get<SystemInfo>(`${API_BASE}/models/system-info`)))
    } catch (error) {
      console.error('Failed to load system info:', error)
    }
  }

  async loadLocalModels(): Promise<void> {
    try {
      this._localModels.set(await firstValueFrom(this.http.get<LocalModel[]>(`${API_BASE}/models/local`)))
    } catch (error) {
      console.error('Failed to load local models:', error)
    }
  }

  /**
   * Download one of the three models, reporting progress in installProgress().
   * `beforeLogin` uses the public bootstrap endpoint, which the server only
   * allows while no model is installed yet.
   */
  async installModel(modelId: string, beforeLogin = false): Promise<void> {
    const url = `${API_BASE}/models/${beforeLogin ? 'bootstrap-install' : 'install'}`
    this._installProgress.set({ modelId, status: 'Starting download…', completed: 0, total: 0, percent: 0 })
    try {
      await this.streamInstall(url, modelId, beforeLogin)
    } finally {
      this._installProgress.set(null)
    }
    if (!beforeLogin) {
      await this.loadLocalModels()
      await this.loadModelStatus()
    }
  }

  private async streamInstall(url: string, modelId: string, beforeLogin: boolean): Promise<void> {
    // fetch() (needed to read the progress stream as it arrives) bypasses
    // HttpClient, so authInterceptor can't add the header here.
    const token = beforeLogin ? null : this.auth.token()
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ model_id: modelId }),
    })
    if (res.status === 401) this.auth.logout()
    if (!res.ok || !res.body) {
      let detail = 'Download request failed'
      try { detail = (await res.json()).detail || detail } catch { /* not JSON */ }
      throw new Error(detail)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let done = false
    while (!done) {
      const chunk = await reader.read()
      if (chunk.done) break
      buf += decoder.decode(chunk.value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        const text = line.replace(/^data:\s*/, '').trim()
        if (!text) continue
        let event: { status?: string; total?: number; completed?: number; error?: string }
        try { event = JSON.parse(text) } catch { continue }
        if (event.error) throw new Error(event.error)
        if (event.status === 'done') { done = true; break }
        const total = event.total ?? 0
        const completed = event.completed ?? 0
        this._installProgress.set({
          modelId, status: event.status ?? '', completed, total,
          percent: total > 0 ? Math.round((completed / total) * 100) : 0,
        })
      }
    }
    if (!done) throw new Error('Download ended before it finished')
  }

  async removeModel(modelId: string): Promise<void> {
    await firstValueFrom(this.http.post(`${API_BASE}/models/remove`, { model_id: modelId }))
    await this.loadLocalModels()
    await this.loadModelStatus()
  }

  /** Switch the signed-in user's model; saved on the account for future sessions. */
  async setActiveModel(modelId: string): Promise<void> {
    await firstValueFrom(this.http.post(`${API_BASE}/models/set-default`, { model_id: modelId }))
    await this.loadLocalModels()
    await this.loadModelStatus()
  }
}
