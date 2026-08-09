import { Component } from '@angular/core'
import { FormsModule } from '@angular/forms'
import { Router } from '@angular/router'
import { CommonModule } from '@angular/common'
import { AuthService } from '../../services/auth.service'

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule, CommonModule],
  template: `
    <div class="auth-shell">
      <div class="auth-card">
        <h2>Knowledge Hubs</h2>
        <p style="color:#999;font-size:0.875rem;margin-top:-0.25rem">{{ mode === 'login' ? 'Sign in to your workspace' : 'Create a new workspace' }}</p>
        <div class="form-stack">
          <input placeholder="Username" [(ngModel)]="username" class="input-field" />
          <input placeholder="Password" type="password" [(ngModel)]="password" class="input-field" />
        </div>
        @if (error) { <p class="error-text">{{ error }}</p> }
        <button class="btn-primary" [disabled]="loading || !username || !password" (click)="submit()">
          {{ loading ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account' }}
        </button>
        <button class="btn-secondary" (click)="toggleMode()">
          {{ mode === 'login' ? 'No account? Register' : 'Have an account? Sign in' }}
        </button>
      </div>
    </div>
  `,
  styles: [`
    .auth-shell { 
      display: flex; 
      justify-content: center; 
      align-items: center; 
      min-height: 100vh;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      padding: 1rem;
    }
    .auth-card {
      background: white;
      border-radius: 12px;
      padding: 2.5rem 2rem;
      max-width: 400px;
      width: 100%;
      box-shadow: 0 10px 40px rgba(0,0,0,0.15);
    }
    .form-stack { display: flex; flex-direction: column; gap: 1rem; margin: 1.5rem 0; }
    .input-field {
      width: 100%; padding: 0.75rem;
      border: 1px solid #d0d0d0; border-radius: 4px; font-size: 0.95rem;
    }
    .input-field:focus {
      outline: none; border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102,126,234,0.15);
    }
    .btn-primary, .btn-secondary {
      width: 100%; padding: 0.75rem 1.5rem; border: none;
      border-radius: 4px; cursor: pointer; font-weight: 600;
      font-size: 0.95rem; transition: opacity 0.2s; justify-content: center;
    }
    .btn-primary { background: #667eea; color: white; }
    .btn-primary:hover:not(:disabled) { opacity: 0.9; }
    .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-secondary { background: #f0f0f0; color: #333; }
    .btn-secondary:hover { background: #e0e0e0; }
  `]
})
export class LoginComponent {
  mode: 'login' | 'register' = 'login'
  username = ''; password = ''; error = ''; loading = false

  constructor(private auth: AuthService, private router: Router) {}

  toggleMode() { this.mode = this.mode === 'login' ? 'register' : 'login'; this.error = '' }

  async submit() {
    this.error = ''; this.loading = true
    try {
      if (this.mode === 'register') {
        await this.auth.register(this.username, this.password)
      }
      await this.auth.login(this.username, this.password)
      this.router.navigate(['/knowledge'])
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Something went wrong'
    } finally { this.loading = false }
  }
}
