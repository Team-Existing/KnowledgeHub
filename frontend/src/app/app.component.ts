import { Component } from '@angular/core'
import { RouterOutlet, RouterLink, Router } from '@angular/router'
import { CommonModule } from '@angular/common'
import { AuthService } from './services/auth.service'
import { ProviderStatusBadgeComponent } from './components/provider-status-badge/provider-status-badge.component'

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, CommonModule, ProviderStatusBadgeComponent],
  templateUrl: './app.component.html'
})
export class AppComponent {
  constructor(public auth: AuthService, private router: Router) {}

  logout() {
    this.auth.logout()
    this.router.navigate(['/login'])
  }
}
