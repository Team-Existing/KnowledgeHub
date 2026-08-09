import { Component, computed } from '@angular/core'
import { RouterOutlet, RouterLink, Router } from '@angular/router'
import { CommonModule } from '@angular/common'
import { AuthService } from './services/auth.service'
import { ProviderStatusBadgeComponent } from './components/provider-status-badge/provider-status-badge.component'

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, CommonModule, ProviderStatusBadgeComponent],
  template: `
    <nav class="nav">
      <h1>Knowledge Hubs</h1>
      <div class="nav-links">
        @if (auth.isLoggedIn()) {
          <a routerLink="/knowledge">Hub</a>
          <a routerLink="/search">Search</a>
          <a routerLink="/review">Review</a>
          <a routerLink="/graphrag">GraphRAG</a>
          <a routerLink="/settings/models">Models</a>
          <app-provider-status-badge></app-provider-status-badge>
          <button style="padding:0.25rem 0.75rem;font-size:0.875rem;background:rgba(255,255,255,0.15);color:#fff;border-color:rgba(255,255,255,0.3)" (click)="logout()">Sign out</button>
        } @else {
          <a routerLink="/login">Sign in</a>
        }
      </div>
    </nav>
    <main>
      <router-outlet />
    </main>
  `
})
export class AppComponent {
  constructor(public auth: AuthService, private router: Router) {}

  logout() {
    this.auth.logout()
    this.router.navigate(['/login'])
  }
}
