import { Injectable, signal, computed } from '@angular/core'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { AuthService, API_BASE } from './auth.service'

export interface LocalModel {
  id: string
  name: string
  size: string
  ramRequired: string
  provider: 'ollama' | 'local'
  installed: boolean
  downloading?: boolean
  progress?: number
}

export interface EmbeddingModel {
  id: string
  name: string
  provider: 'local'
}

export interface ModelStatus {
  llm: {
    provider: 'local' | 'cloud'
    model?: string
    installed?: boolean
  }
  embedding: {
    provider: 'local'
    model?: string
    installed?: boolean
  }
  cloudEnabled?: boolean
}

export interface SystemInfo {
  ramGb: number
  recommendedTier: string
}

export interface InstallProgress {
  status: string
  completed: number
  total: number
  percent: number
  done: boolean
  error?: string
}

@Injectable({ providedIn: 'root' })
export class ModelService {
  private _modelStatus = signal<ModelStatus | null>(null)
  private _localModels = signal<LocalModel[]>([])
  private _systemInfo = signal<SystemInfo | null>(null)
  private _cloudEnabled = signal<boolean>(false)
  private _initialized = signal<boolean>(false)
  private _installProgress = signal<InstallProgress | null>(null)

  readonly modelStatus = this._modelStatus.asReadonly()
  readonly localModels = this._localModels.asReadonly()
  readonly systemInfo = this._systemInfo.asReadonly()
  readonly cloudEnabled = this._cloudEnabled.asReadonly()
  readonly initialized = this._initialized.asReadonly()
  readonly installProgress = this._installProgress.asReadonly()

  readonly currentLlmProvider = computed(() => {
    const status = this._modelStatus()
    if (!status) return 'not-configured'
    return status.llm.provider === 'local' ? 'local' : 'cloud'
  })

  readonly currentLlmModel = computed(() => {
    const status = this._modelStatus()
    return status?.llm.model || 'Not configured'
  })

  readonly statusBadgeColor = computed(() => {
    const status = this._modelStatus()
    if (!status) return 'red'
    if (status.llm.provider === 'cloud') return 'blue'
    return status.llm.installed ? 'green' : 'red'
  })

  readonly statusBadgeText = computed(() => {
    const status = this._modelStatus()
    if (!status) return '🔴 Nothing activated'
    if (status.llm.provider === 'cloud') return `🔵 Cloud · ${status.llm.model}`
    if (status.llm.installed) return `🟢 Local · ${status.llm.model}`
    return '🔴 Nothing activated'
  })

  constructor(private http: HttpClient, private auth: AuthService) {}

  async loadAll(): Promise<void> {
    this._initialized.set(false)
    await Promise.all([this.loadLocalModels(), this.loadModelStatus(), this.loadSystemInfo()])
    this._initialized.set(true)
  }

  async loadModelStatus(): Promise<void> {
    try {
      const status = await firstValueFrom(
        this.http.get<ModelStatus>(`${API_BASE}/models/status`, { headers: this.auth.authHeaders() })
      )
      this._modelStatus.set(status)
      this._cloudEnabled.set(!!status.cloudEnabled)
    } catch (error) {
      console.error('Failed to load model status:', error)
    }
  }

  async loadSystemInfo(): Promise<void> {
    try {
      const info = await firstValueFrom(
        this.http.get<SystemInfo>(`${API_BASE}/models/system-info`, { headers: this.auth.authHeaders() })
      )
      this._systemInfo.set(info)
    } catch (error) {
      console.error('Failed to load system info:', error)
    }
  }

  async loadLocalModels(): Promise<void> {
    try {
      const models = await firstValueFrom(
        this.http.get<LocalModel[]>(`${API_BASE}/models/local`, { headers: this.auth.authHeaders() })
      )
      this._localModels.set(models)
    } catch (error) {
      console.error('Failed to load local models:', error)
    }
  }

  installModel(modelId: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const token = this.auth.authHeaders()['Authorization'] ?? ''
      fetch(`${API_BASE}/models/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: token },
        body: JSON.stringify({ model_id: modelId }),
      }).then(async res => {
        if (!res.ok || !res.body) { reject(new Error('Install request failed')); return }
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const lines = buf.split('\n')
          buf = lines.pop() ?? ''
          for (const line of lines) {
            const trimmed = line.replace(/^data:\s*/, '').trim()
            if (!trimmed) continue
            try {
              const chunk = JSON.parse(trimmed)
              if (chunk.error) { reject(new Error(chunk.error)); return }
              const total = chunk.total ?? 0
              const completed = chunk.completed ?? 0
              const percent = total > 0 ? Math.round((completed / total) * 100) : 0
              this._installProgress.set({
                status: chunk.status ?? '',
                completed, total, percent,
                done: chunk.status === 'done' || chunk.status === 'success',
              })
            } catch { /* skip malformed */ }
          }
        }
        this._installProgress.set(null)
        await this.loadLocalModels()
        await this.loadModelStatus()
        resolve()
      }).catch(reject)
    })
  }

  async removeModel(modelId: string): Promise<void> {
    await firstValueFrom(
      this.http.post(`${API_BASE}/models/remove`, { model_id: modelId }, { headers: this.auth.authHeaders() })
    )
    await this.loadLocalModels()
    await this.loadModelStatus()
  }

  async setDefaultModel(modelId: string): Promise<void> {
    await firstValueFrom(
      this.http.post(`${API_BASE}/models/set-default`, { model_id: modelId }, { headers: this.auth.authHeaders() })
    )
    await this.loadModelStatus()
  }

  async toggleCloudProvider(enabled: boolean): Promise<void> {
    try {
      this._cloudEnabled.set(enabled)
      await firstValueFrom(
        this.http.patch(`${API_BASE}/workspace/settings`, { allow_cloud_providers: enabled }, { headers: this.auth.authHeaders() })
      )
      await this.loadModelStatus()
    } catch (error) {
      console.error('Failed to toggle cloud provider:', error)
      this._cloudEnabled.set(!enabled)
    }
  }

  /**
   * POST the real API key to the backend, which Fernet-encrypts it before
   * writing to the database. The plaintext never touches disk.
   * Returns the opaque config_id ref the caller can store client-side.
   */
  async saveApiKey(provider: string, apiKey: string): Promise<string> {
    const data: any = await firstValueFrom(
      this.http.post(
        `${API_BASE}/workspace/api-key`,
        { provider, api_key: apiKey },
        { headers: this.auth.authHeaders() }
      )
    )
    await this.loadModelStatus()
    return data.config_id as string
  }
}
