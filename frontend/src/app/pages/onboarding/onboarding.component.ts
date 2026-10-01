import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { Router, RouterLink } from '@angular/router'
import { ModelCatalog, ModelService } from '../../services/model.service'
import { AuthService } from '../../services/auth.service'
import { ModelSetupComponent } from '../../components/model-setup/model-setup.component'
import { errorMessage } from '../../services/http-error'

/**
 * First-run flow: Welcome → Download a model (only if none of the three is
 * installed) → Create account → Done. A model is required before the account
 * step, because signing in needs one.
 */
type Step = 'welcome' | 'model' | 'account' | 'done'

@Component({
  selector: 'app-onboarding',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, ModelSetupComponent],
  templateUrl: './onboarding.component.html',
  styleUrl: './onboarding.component.css',
})
export class OnboardingComponent implements OnInit {
  step: Step = 'welcome'
  catalog: ModelCatalog | null = null
  checking = true
  username = ''
  password = ''
  isCreating = false
  setupError = ''

  constructor(private auth: AuthService, private models: ModelService, private router: Router) {}

  ngOnInit() { this.refreshCatalog() }

  async refreshCatalog() {
    this.checking = true
    try {
      this.catalog = await this.models.getCatalog()
    } catch (e) {
      this.setupError = errorMessage(e, 'Could not reach the Knowledge Hubs server')
    } finally {
      this.checking = false
    }
  }

  get needsModel(): boolean {
    return !this.catalog || !this.catalog.ollamaReachable || !this.catalog.anyInstalled
  }

  /** Steps shown to this user, for "Step n of m". */
  get steps(): Step[] {
    return this.needsModel || this.step === 'model' ? ['welcome', 'model', 'account'] : ['welcome', 'account']
  }

  get stepNumber(): number { return this.steps.indexOf(this.step) + 1 }

  async start() {
    await this.refreshCatalog()
    this.step = this.needsModel ? 'model' : 'account'
  }

  onInstalled() { this.step = 'account' }

  async createAccount() {
    this.isCreating = true
    this.setupError = ''
    try {
      await this.auth.registerAndLogin(this.username, this.password)
      this.step = 'done'
    } catch (e) {
      this.setupError = errorMessage(e, 'Could not create account')
    } finally {
      this.isCreating = false
    }
  }

  goToApp() { this.router.navigate(['/knowledge']) }
}
