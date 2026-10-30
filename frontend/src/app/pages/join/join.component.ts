import { Component, OnInit } from '@angular/core'
import { ActivatedRoute, Router, RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { SpaceService } from '../../services/space.service'
import { errorMessage } from '../../services/http-error'
import { InvitePreview, Organization } from '../../models/api'

/** An invite link (/join/<token>): what it joins, and a button to join. Signing in comes first (authGuard). */
@Component({
  selector: 'app-join',
  imports: [RouterLink],
  templateUrl: './join.component.html'
})
export class JoinComponent implements OnInit {
  preview: InvitePreview | null = null
  error = ''; loading = true; busy = false
  private token = ''

  constructor(route: ActivatedRoute, private http: HttpClient, private spaces: SpaceService, private router: Router) {
    this.token = route.snapshot.paramMap.get('token') ?? ''
  }

  async ngOnInit() {
    try {
      this.preview = await firstValueFrom(this.http.get<InvitePreview>(
        `${API_BASE}/organizations/join/${encodeURIComponent(this.token)}`))
    } catch (e) { this.error = errorMessage(e, 'This invite link is invalid or has expired') }
    finally { this.loading = false }
  }

  async join() {
    this.busy = true; this.error = ''
    try {
      const org = await firstValueFrom(this.http.post<Organization>(
        `${API_BASE}/organizations/join/${encodeURIComponent(this.token)}`, {}))
      await this.open(org.id)
    } catch (e) { this.error = errorMessage(e, 'Could not join') }
    finally { this.busy = false }
  }

  async open(orgId: string) {
    await this.spaces.load()
    await this.router.navigateByUrl('/knowledge')
    await this.spaces.switchTo(orgId)
  }
}
