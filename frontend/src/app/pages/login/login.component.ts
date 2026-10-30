import { Component, OnInit } from '@angular/core'
import { FormsModule } from '@angular/forms'
import { ActivatedRoute, Router } from '@angular/router'
import { HttpErrorResponse } from '@angular/common/http'
import { AuthService } from '../../services/auth.service'
import { hostedProviders, ModelCatalog, ModelService } from '../../services/model.service'
import { ModelSetupComponent } from '../../components/model-setup/model-setup.component'
import { errorMessage } from '../../services/http-error'
import { IconComponent } from '../../components/icon/icon.component'
import { PASSWORD_RULES } from '../../services/usernames'

/**
 * Sign-in starts with a model check: a model must be usable (an installed
 * local model, or one of the server's API models). If none is, the download
 * screen is shown first; otherwise the user goes straight to the sign-in form.
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
  // a fresh server with SETUP_TOKEN set: the first account (the admin) needs it
  needsSetupToken = false; setupToken = ''
  readonly passwordRules = PASSWORD_RULES
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

  ngOnInit() {
    this.checkModels()
    this.auth.setupStatus().then(s => {
      this.needsSetupToken = s.setup_token_required
      if (s.setup_token_required) this.mode = 'register'
    }).catch(() => { /* older server, or unreachable: checkModels reports that */ })
  }

  async checkModels() {
    this.stage = 'checking'
    try {
      this.catalog = await this.models.getCatalog()
      this.stage = this.catalog.anyInstalled ? 'ready' : 'needs-model'
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

  /** Services hosted models send text to; empty when every model is local */
  get hosted(): string[] { return this.catalog ? hostedProviders(this.catalog.models) : [] }

  toggleMode() { this.mode = this.mode === 'login' ? 'register' : 'login'; this.error = '' }

  async submit() {
    this.error = ''; this.loading = true
    try {
      if (this.mode === 'register') {
        await this.auth.register(this.username, this.password, this.needsSetupToken ? this.setupToken : undefined)
        this.needsSetupToken = false
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
