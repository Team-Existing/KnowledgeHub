import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { COMMITMENT_LABEL, VERDICT_LABEL } from '../../services/tracking'
import { CommitmentEntry, CommitmentsResponse, CommitmentStatus, OutcomesResponse } from '../../models/api'

type Show = 'open' | 'overdue' | 'done' | 'all'

/** What decisions set in motion (commitments) and whether they worked (outcomes). */
@Component({
  selector: 'app-commitments',
  imports: [CommonModule, RouterLink],
  templateUrl: './commitments.component.html'
})
export class CommitmentsComponent implements OnInit {
  tab: 'commitments' | 'outcomes' = 'commitments'
  show: Show = 'open'
  data: CommitmentsResponse | null = null
  outcomes: OutcomesResponse | null = null
  error = ''; busy = false; loading = true

  readonly statuses: CommitmentStatus[] = ['open', 'in_progress', 'done', 'dropped']
  readonly statusLabel = COMMITMENT_LABEL
  readonly verdictLabel = VERDICT_LABEL
  readonly filters: { key: Show; label: string }[] = [
    { key: 'open', label: 'Open' }, { key: 'overdue', label: 'Overdue' },
    { key: 'done', label: 'Finished' }, { key: 'all', label: 'All' },
  ]

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.load() }

  async load() {
    this.loading = true; this.error = ''
    try {
      const [data, outcomes] = await Promise.all([
        firstValueFrom(this.http.get<CommitmentsResponse>(`${API_BASE}/knowledge/commitments`,
          { params: { show: this.show, limit: 500 } })),
        firstValueFrom(this.http.get<OutcomesResponse>(`${API_BASE}/knowledge/outcomes`)),
      ])
      this.data = data; this.outcomes = outcomes
    } catch (e) { this.error = errorMessage(e, 'Could not load commitments') }
    finally { this.loading = false }
  }

  async filter(show: Show) { this.show = show; await this.load() }

  count(key: Show): number {
    const c = this.data?.counts
    if (!c) return 0
    return key === 'open' ? c.open + c.in_progress : key === 'overdue' ? c.overdue
      : key === 'done' ? c.done + c.dropped : c.open + c.in_progress + c.done + c.dropped
  }

  async setStatus(c: CommitmentEntry, status: CommitmentStatus) {
    this.busy = true; this.error = ''
    try {
      await firstValueFrom(this.http.put(`${API_BASE}/knowledge/items/${encodeURIComponent(c.id)}/status`, { status, note: '' }))
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not change the status') }
    finally { this.busy = false }
  }
}
