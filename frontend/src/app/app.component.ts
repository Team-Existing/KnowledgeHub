import { Component, OnDestroy, effect, signal, untracked } from '@angular/core'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { HealthResponse } from './models/api'
import { RouterOutlet, RouterLink, Router } from '@angular/router'
import { CommonModule } from '@angular/common'
import { API_BASE, AuthService } from './services/auth.service'
import { SpaceService } from './services/space.service'
import { ProviderStatusBadgeComponent } from './components/provider-status-badge/provider-status-badge.component'

/** How often the nav checks for new group invitations while signed in. */
const REFRESH_MS = 60_000

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, CommonModule, ProviderStatusBadgeComponent],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnDestroy {
  private timer: ReturnType<typeof setInterval> | null = null
  private healthTimer: ReturnType<typeof setInterval> | null = null
  /** null = fine; otherwise what to tell the user (GET /health, public, checked every minute) */
  readonly healthProblem = signal<string | null>(null)

  constructor(public auth: AuthService, public spaces: SpaceService, private router: Router,
              private http: HttpClient) {
    this.auth.loadMe()
    this.checkHealth()
    this.healthTimer = setInterval(() => this.checkHealth(), REFRESH_MS)
    // load spaces and invitations whenever someone signs in; clear them on sign-out
    effect(() => {
      const signedIn = this.auth.isLoggedIn()
      untracked(() => {
        if (this.timer) { clearInterval(this.timer); this.timer = null }
        if (signedIn) {
          this.spaces.load()
          this.timer = setInterval(() => this.spaces.load(), REFRESH_MS)
        } else {
          this.spaces.reset()
        }
      })
    }, { allowSignalWrites: true })
  }

  ngOnDestroy() {
    if (this.timer) clearInterval(this.timer)
    if (this.healthTimer) clearInterval(this.healthTimer)
  }

  async checkHealth() {
    try {
      const h = await firstValueFrom(this.http.get<HealthResponse>(`${API_BASE}/health`))
      this.healthProblem.set(h.arcadedb === 'connected' ? null
        : 'The database is unreachable, so nothing can be loaded or saved right now. Ask whoever runs this hub to start ArcadeDB.')
    } catch {
      this.healthProblem.set('Cannot reach the Knowledge Hubs server. Check that the backend is running.')
    }
  }

  onSpaceChange(id: string) { this.spaces.switchTo(id) }

  logout() {
    this.auth.logout()
    this.router.navigate(['/login'])
  }
}
