import { Component, OnInit } from '@angular/core'
import { FormsModule } from '@angular/forms'
import { ActivatedRoute, Router } from '@angular/router'
import { HttpErrorResponse } from '@angular/common/http'
import { AuthService } from '../../services/auth.service'
import { ModelCatalog, ModelService } from '../../services/model.service'
import { ModelSetupComponent } from '../../components/model-setup/model-setup.component'
import { errorMessage } from '../../services/http-error'
import { IconComponent } from '../../components/icon/icon.component'

/**
 * Sign-in starts with a model check: one of the three local models must be
 * installed. If none is, the download screen is shown first; if one already
 * is, the user goes straight to the sign-in form.
 */
type Stage = 'checking' | 'needs-model' | 'ready'

@Component({
  selector: 'app-login',
  imports: [FormsModule, ModelSetupComponent, IconComponent],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css'
})
export class LoginComponent implements OnInit {
  stage: Stage = 'checking'
  catalog: ModelCatalog | null = null
  justInstalled = ''
  mode: 'login' | 'register' = 'login'
  username = ''; password = ''; error = ''; loading = false
  // set by authInterceptor when the API rejected a stored token
  readonly sessionExpired: boolean

  constructor(
    private auth: AuthService,
    private models: ModelService,
    private router: Router,
    route: ActivatedRoute,
  ) {
    this.sessionExpired = route.snapshot.queryParamMap.has('expired')
  }

  ngOnInit() { this.checkModels() }

  async checkModels() {
    this.stage = 'checking'
    try {
      this.catalog = await this.models.getCatalog()
      this.stage = this.catalog.ollamaReachable && this.catalog.anyInstalled ? 'ready' : 'needs-model'
    } catch (e) {
      this.catalog = null
      this.error = errorMessage(e, 'Could not reach the Knowledge Hubs server')
      this.stage = 'ready'
    }
  }

  onInstalled(modelId: string) {
    this.justInstalled = this.catalog?.models.find(m => m.id === modelId)?.name ?? modelId
    this.stage = 'ready'
  }

  toggleMode() { this.mode = this.mode === 'login' ? 'register' : 'login'; this.error = '' }

  async submit() {
    this.error = ''; this.loading = true
    try {
      if (this.mode === 'register') {
        await this.auth.register(this.username, this.password)
      }
      await this.auth.login(this.username, this.password)
      this.router.navigate(['/knowledge'])
    } catch (e) {
      // 412: no model installed, 503: Ollama down (e.g. changed since the page loaded)
      if (e instanceof HttpErrorResponse && (e.status === 412 || e.status === 503)) {
        await this.checkModels()
      }
      this.error = errorMessage(e, 'Something went wrong')
    } finally { this.loading = false }
  }
}
