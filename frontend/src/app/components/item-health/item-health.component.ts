import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { describeSignal, HEALTH_LABEL } from '../../services/health'
import { KnowledgeItem } from '../../models/api'

const RETIRED = ['superseded', 'reversed', 'rejected', 'deprecated', 'closed']

/** Whether the item may be out of date, and the actions to flag it, confirm it or set a review date. */
@Component({
  selector: 'app-item-health',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './item-health.component.html'
})
export class ItemHealthComponent implements OnChanges {
  @Input({ required: true }) item!: KnowledgeItem
  /** Fired after a change, so the page can reload the item */
  @Output() changed = new EventEmitter<void>()

  readonly label = HEALTH_LABEL
  readonly describe = describeSignal
  mode: '' | 'verify' | 'flag' = ''
  note = ''; reviewDue = ''
  error = ''; busy = false

  constructor(private http: HttpClient) {}

  ngOnChanges() { this.reviewDue = this.item.health?.review_due ?? '' }

  get retired(): boolean { return !!this.item.status && RETIRED.includes(this.item.status) }

  open(mode: 'verify' | 'flag') {
    this.mode = mode; this.note = ''; this.error = ''
    // confirming starts from the current review date only if it's still ahead
    const due = this.item.health?.review_due
    this.reviewDue = mode === 'verify' && due && !this.item.health.overdue && this.item.health.state === 'ok' ? due : ''
  }

  async verify() {
    await this.run('Could not confirm', () => firstValueFrom(this.http.post(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/health/verify`,
      { note: this.note, review_due: this.reviewDue || null })))
  }

  async flag() {
    if (this.note.trim().length < 3) { this.error = 'Say why it may be out of date'; return }
    await this.run('Could not flag', () => firstValueFrom(this.http.post(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/health/flag`, { note: this.note })))
  }

  async saveReviewDate() {
    await this.run('Could not set the review date', () => firstValueFrom(this.http.put(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/review-date`,
      { review_due: this.reviewDue || null })))
  }

  private async run(failure: string, call: () => Promise<unknown>) {
    this.busy = true; this.error = ''
    try {
      await call()
      this.mode = ''; this.note = ''
      this.changed.emit()
    } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
