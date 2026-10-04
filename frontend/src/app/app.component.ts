import { Component, OnDestroy, computed, effect, signal, untracked } from '@angular/core'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom, filter } from 'rxjs'
import { HealthResponse } from './models/api'
import { RouterOutlet, RouterLink, RouterLinkActive, Router, NavigationEnd } from '@angular/router'
import { CommonModule } from '@angular/common'
import { API_BASE, AuthService } from './services/auth.service'
import { SpaceService } from './services/space.service'
import { ProviderStatusBadgeComponent } from './components/provider-status-badge/provider-status-badge.component'
import { IconComponent } from './components/icon/icon.component'

/** How often the nav checks for new group invitations while signed in. */
const REFRESH_MS = 60_000
const THEME_KEY = 'kh_theme'
type Theme = 'system' | 'light' | 'dark'

interface NavItem { path: string; label: string; icon: string; badge?: () => number; show?: () => boolean }

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule, ProviderStatusBadgeComponent, IconComponent],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnDestroy {
  private timer: ReturnType<typeof setInterval> | null = null
  private healthTimer: ReturnType<typeof setInterval> | null = null
  /** null = fine; otherwise what to tell the user (GET /health, public, checked every minute) */
  readonly healthProblem = signal<string | null>(null)
  readonly url = signal('/')
  readonly menuOpen = signal(false)
  readonly theme = signal<Theme>(readTheme())

  /** Sign-in and onboarding are full-screen pages without the app shell. */
  readonly bare = computed(() => !this.auth.isLoggedIn() || /^\/(login|onboarding)(\/|\?|$)/.test(this.url()))

  readonly sections: { label: string; items: NavItem[] }[] = [
    { label: 'Knowledge', items: [
      { path: '/knowledge', label: 'Hub', icon: 'hub' },
      { path: '/search', label: 'Search', icon: 'search' },
      { path: '/review', label: 'Review', icon: 'review' },
      { path: '/decisions', label: 'Decisions', icon: 'decisions' },
      { path: '/playbooks', label: 'Playbooks', icon: 'playbooks' },
    ] },
    { label: 'Ask', items: [
      { path: '/graphrag', label: 'GraphRAG', icon: 'ask' },
    ] },
    { label: 'Collaborate', items: [
      { path: '/connectors', label: 'Sources', icon: 'sources' },
      { path: '/groups', label: 'Groups', icon: 'groups', badge: () => this.spaces.invitations().length },
    ] },
    { label: 'Settings', items: [
      { path: '/settings/models', label: 'Models', icon: 'models' },
      { path: '/admin/users', label: 'Users', icon: 'users', show: () => this.auth.can('manage_users') },
    ] },
  ]

  constructor(public auth: AuthService, public spaces: SpaceService, private router: Router,
              private http: HttpClient) {
    this.auth.loadMe()
    this.applyTheme(this.theme())
    this.checkHealth()
    this.healthTimer = setInterval(() => this.checkHealth(), REFRESH_MS)
    this.router.events.pipe(filter(e => e instanceof NavigationEnd)).subscribe(e => {
      this.url.set((e as NavigationEnd).urlAfterRedirects)
      this.menuOpen.set(false)       // a tap on a nav item closes the mobile menu
    })
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

  initials(): string {
    const name = this.auth.me()?.username ?? ''
    const parts = name.split(/[._\-\s]+/).filter(Boolean)
    return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? parts[0]?.[1] ?? '')) || '?'
  }

  setTheme(theme: Theme) {
    this.theme.set(theme)
    try { theme === 'system' ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, theme) } catch { /* not persisted */ }
    this.applyTheme(theme)
  }

  private applyTheme(theme: Theme) {
    const root = document.documentElement
    if (theme === 'system') delete root.dataset['theme']
    else root.dataset['theme'] = theme
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

function readTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY)
    return t === 'light' || t === 'dark' ? t : 'system'
  } catch { return 'system' }
}
