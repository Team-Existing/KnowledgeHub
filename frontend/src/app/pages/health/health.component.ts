import { DigestSettingsComponent } from '../../components/digest-settings/digest-settings.component'
import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient, HttpParams } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { describeSignal, HEALTH_LABEL } from '../../services/health'
import { KnowledgeHealthResponse, HealthState, KnowledgeItem } from '../../models/api'

/** Current items that may be out of date or are due for review, most urgent first. */
@Component({
  selector: 'app-health',
  imports: [CommonModule, FormsModule, RouterLink, DigestSettingsComponent],
  templateUrl: './health.component.html'
})
export class HealthComponent implements OnInit {
  readonly label = HEALTH_LABEL
  readonly describe = describeSignal
  items: KnowledgeItem[] = []
  counts: KnowledgeHealthResponse['counts'] = { potentially_stale: 0, needs_review: 0, overdue: 0 }
  total = 0
  filter: '' | HealthState = ''
  error = ''; loading = false; busyId = ''
  private readonly pageSize = 100

  constructor(private http: HttpClient) {}

  ngOnInit() { this.load() }

  get visible(): KnowledgeItem[] {
    return this.filter ? this.items.filter(i => i.health.state === this.filter) : this.items
  }

  async load(more = false) {
    this.loading = true; this.error = ''
    try {
      const params = new HttpParams().set('limit', this.pageSize).set('offset', more ? this.items.length : 0)
      const r = await firstValueFrom(this.http.get<KnowledgeHealthResponse>(`${API_BASE}/knowledge/health`, { params }))
      this.items = more ? [...this.items, ...r.items] : r.items
      this.total = r.total; this.counts = r.counts
    } catch (e) { this.error = errorMessage(e, 'Could not load what needs a look') }
    finally { this.loading = false }
  }

  /** Confirm without a note or a new review date; the item page offers both. */
  async stillValid(item: KnowledgeItem) {
    this.busyId = item.id; this.error = ''
    try {
      await firstValueFrom(this.http.post(
        `${API_BASE}/knowledge/items/${encodeURIComponent(item.id)}/health/verify`, { note: '', review_due: null }))
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not confirm') }
    finally { this.busyId = '' }
  }
}
