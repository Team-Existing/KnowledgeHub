import { Component } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { Router } from '@angular/router'
import { ModelService } from '../../services/model.service'
import { AuthService } from '../../services/auth.service'

@Component({
  selector: 'app-onboarding',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="onboarding-container">
      <div class="onboarding-content">
        <!-- Step 1: Welcome -->
        @if (currentStep === 1) {
          <div class="step">
            <h1>Welcome to Knowledge Hubs</h1>
            <p class="step-number">Step 1 of 3</p>
            <div class="welcome-message">
              <p>Knowledge Hubs runs fully on your machine.</p>
              <p><strong>No API key needed.</strong></p>
              <p>Your data stays private. All AI processing happens locally.</p>
            </div>
            <button class="btn-primary" (click)="nextStep()">Get Started</button>
          </div>
        }

        <!-- Step 2: Create Account (No workspace) -->
        @if (currentStep === 2) {
          <div class="step">
            <h1>Create Your Account</h1>
            <p class="step-number">Step 2 of 3</p>
            
            <div class="form-group">
              <label>Username</label>
              <input type="text" [(ngModel)]="username" placeholder="Choose a username" class="input-field" />
            </div>
            <div class="form-group">
              <label>Password</label>
              <input type="password" [(ngModel)]="password" placeholder="Choose a password" class="input-field" />
            </div>
            
            @if (setupError) { <p class="error-text">{{ setupError }}</p> }
            
            <div class="step-actions">
              <button class="btn-secondary" (click)="previousStep()">Back</button>
              <button class="btn-primary" [disabled]="!username || !password || isCreating" (click)="createAccount()">
                {{ isCreating ? 'Creating Account...' : 'Create Account' }}
              </button>
            </div>
          </div>
        }

        <!-- Step 3: Optional Model Installation -->
        @if (currentStep === 3) {
          <div class="step">
            <h1>Install a Model (Optional)</h1>
            <p class="step-number">Step 3 of 3</p>
            <p class="info-text">
              You can skip this and install models later from the Model Manager.
            </p>
            
            <div class="recommended-models">
              @for (model of recommendedModels; track model.id) {
                <div class="model-option" [class.selected]="selectedModel === model.id">
                  <label>
                    <input type="radio" [value]="model.id" [(ngModel)]="selectedModel" />
                    <span class="model-name">{{ model.name }}</span>
                    <span class="model-details">{{ model.size }} • {{ model.ramRequired }}</span>
                  </label>
                </div>
              }
            </div>
            
            @if (installError) { <p class="error-text">{{ installError }}</p> }
            
            <div class="step-actions">
              <button class="btn-secondary" (click)="previousStep()">Back</button>
              <button class="btn-secondary" (click)="skipModelInstall()">Skip</button>
              <button class="btn-primary" [disabled]="isInstalling || !selectedModel" (click)="installSelectedModel()">
                {{ isInstalling ? 'Installing...' : 'Install Model' }}
              </button>
            </div>
          </div>
        }

        <!-- Step 4: Done -->
        @if (currentStep === 4) {
          <div class="step">
            <h1>You're All Set! 🎉</h1>
            <div class="welcome-message">
              <p>Your knowledge hub is ready to use.</p>
              <p>Start by ingesting your first document or transcript.</p>
            </div>
            <button class="btn-primary" (click)="goToApp()">Go to Knowledge Hub</button>
          </div>
        }

        <!-- Installation overlay -->
        @if (isInstalling) {
          <div class="progress-overlay">
            <div class="progress-card">
              <div class="spinner"></div>
              <h2>Installing {{ selectedModel }}…</h2>
              <p class="info-text">This may take several minutes depending on your connection.</p>
            </div>
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    /* Keep existing styles, remove workspace-specific ones */
    .onboarding-container {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      padding: 2rem;
    }

    .onboarding-content {
      background: white;
      border-radius: 12px;
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
      max-width: 500px;
      width: 100%;
      padding: 3rem 2rem;
    }

    .step { animation: slideIn 0.3s ease-out; }
    @keyframes slideIn {
      from { opacity: 0; transform: translateY(10px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    h1 { font-size: 2rem; margin: 0 0 0.5rem; color: #333; }
    .step-number { color: #999; font-size: 0.875rem; margin: 0 0 2rem; }

    .welcome-message {
      background: #f0f8ff;
      border-left: 4px solid #667eea;
      padding: 1.5rem;
      border-radius: 4px;
      margin: 2rem 0;
      line-height: 1.6;
    }

    .form-group { margin: 1.5rem 0; }
    .form-group label { display: block; margin-bottom: 0.5rem; font-weight: 500; }

    .input-field {
      width: 100%; padding: 0.75rem;
      border: 1px solid #d0d0d0; border-radius: 4px; font-size: 0.95rem;
    }
    .input-field:focus {
      outline: none; border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }

    .recommended-models { margin: 1.5rem 0; }
    .model-option {
      border: 2px solid #e0e0e0;
      border-radius: 8px;
      padding: 1rem;
      margin-bottom: 0.75rem;
      cursor: pointer;
      transition: all 0.2s;
    }
    .model-option:hover { border-color: #667eea; background: #f9f9f9; }
    .model-option.selected { border-color: #667eea; background: #f0f8ff; }
    .model-option label { display: flex; flex-direction: column; gap: 0.5rem; cursor: pointer; }
    .model-name { font-weight: 500; font-size: 0.95rem; }
    .model-details { font-size: 0.8rem; color: #666; }

    .info-text { font-size: 0.85rem; color: #666; margin: 0.75rem 0; }
    .error-text { font-size: 0.85rem; color: #b42318; margin: 0.5rem 0 0; }

    .step-actions { display: flex; gap: 1rem; margin-top: 2rem; }

    .btn-primary, .btn-secondary {
      flex: 1; padding: 0.75rem 1.5rem; border: none;
      border-radius: 4px; cursor: pointer; font-weight: 600;
      font-size: 0.95rem; transition: opacity 0.2s;
    }
    .btn-primary { background: #667eea; color: white; }
    .btn-primary:hover:not(:disabled) { opacity: 0.9; }
    .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-secondary { background: #f0f0f0; color: #333; }
    .btn-secondary:hover { background: #e0e0e0; }

    /* Install overlay */
    .progress-overlay {
      position: fixed; inset: 0;
      background: rgba(0, 0, 0, 0.5);
      display: flex; align-items: center; justify-content: center;
      z-index: 1000;
    }
    .progress-card {
      background: white; border-radius: 12px;
      padding: 2rem; text-align: center; max-width: 320px; width: 100%;
    }
    .progress-card h2 { font-size: 1.1rem; margin: 1rem 0 0.5rem; }

    .spinner {
      width: 48px; height: 48px; margin: 0 auto;
      border: 4px solid #e0e0e0;
      border-top-color: #667eea;
      border-radius: 50%;
      animation: spin 0.9s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class OnboardingComponent {
  currentStep = 1
  username = ''
  password = ''
  selectedModel: string | null = null
  isCreating = false
  isInstalling = false
  setupError = ''
  installError = ''

  readonly recommendedModels = [
    { id: 'llama3.1:8b', name: 'Llama 3.1 8B (Recommended)', size: '4.7 GB', ramRequired: '8 GB+', provider: 'ollama', minRam: 8 },
    { id: 'mistral:7b', name: 'Mistral 7B', size: '4.1 GB', ramRequired: '8 GB+', provider: 'ollama', minRam: 8 },
    { id: 'gpt-oss:20b', name: 'GPT-OSS 20B', size: '13 GB', ramRequired: '16 GB+', provider: 'ollama', minRam: 16 },
  ]

  constructor(
    private auth: AuthService,
    private modelService: ModelService,
    private router: Router
  ) {}

  nextStep() { if (this.currentStep < 4) this.currentStep++ }
  previousStep() { if (this.currentStep > 1) this.currentStep-- }

  async createAccount() {
    this.isCreating = true
    this.setupError = ''
    try {
      await this.auth.registerAndLogin(this.username, this.password)
      this.nextStep()
    } catch (e: any) {
      this.setupError = e?.error?.detail || e?.message || 'Could not create account'
    } finally {
      this.isCreating = false
    }
  }

  async installSelectedModel() {
    if (!this.selectedModel) {
      this.installError = 'Please select a model first'
      return
    }

    this.isInstalling = true
    this.installError = ''
    try {
      await this.modelService.installModel(this.selectedModel)
      this.nextStep()
    } catch (e: any) {
      this.installError = e?.message || 'Installation failed. You can try later from Model Manager.'
    } finally {
      this.isInstalling = false
    }
  }

  skipModelInstall() {
    this.nextStep()
  }

  goToApp() {
    this.router.navigate(['/knowledge'])
  }
}