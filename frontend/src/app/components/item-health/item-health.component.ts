import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { describeSignal, HEALTH_LABEL } from '../../services/health'
import { ConsistencyCheck, KnowledgeItem, SupportCheck } from '../../models/api'
import { SupportNoteComponent } from '../support-note/support-note.component'

const RETIRED = ['superseded', 'reversed', 'rejected', 'deprecated', 'closed']

/** Whether the item may be out of date, and the actions to flag it, confirm it or set a review date. */
@Component({
  selector: 'app-item-health',
  imports: [CommonModule, FormsModule, RouterLink, SupportNoteComponent],
  templateUrl: './item-health.component.html'
})
export class ItemHealthComponent implements OnChanges {
  @Input({ required: true }) item!: KnowledgeItem
  /** Fired after a change, so the page can reload the item */
  @Output() changed = new EventEmitter<void>()

  readonly label = HEALTH_LABEL
  readonly describe = describeSignal
  mode: '' | 'verify' | 'flag' = ''
  /** what the last consistency check found, in words */
  checkResult = ''
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

  /** Duplicates and contradictions among similar current knowledge (backend services/verification.py) */
  async checkConsistency() {
    this.checkResult = ''
    await this.run('Could not check', async () => {
      const r = await firstValueFrom(this.http.post<ConsistencyCheck>(
        `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/health/check`, {}))
      const found = r.contradictions.length + r.duplicates.length
      this.checkResult = !r.checked ? 'No similar current knowledge to compare with.'
        : found ? `Found ${r.contradictions.length} contradiction${r.contradictions.length === 1 ? '' : 's'} and ${r.duplicates.length} possible duplicate${r.duplicates.length === 1 ? '' : 's'}; see the signals above.`
        : `Compared with ${r.checked} similar item${r.checked === 1 ? '' : 's'}: ${r.llm_used ? 'no contradictions or duplicates' : 'no duplicates (no model was available to look for contradictions)'}.`
    })
  }

  /** Whether the item's source says what it claims (backend services/grounding.py) */
  async checkSupport() {
    await this.run('Could not check against the source', () => firstValueFrom(this.http.post<SupportCheck>(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/support/check`, {})))
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
