import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { COMMITMENT_LABEL, VERDICT_LABEL } from '../../services/tracking'
import { CommitmentStatus, KnowledgeItem, Verdict } from '../../models/api'

/**
 * An action item's commitment (status, owner, due date), or a decision's
 * outcome (what it should achieve, when to check, and the verdict).
 */
@Component({
  selector: 'app-item-tracking',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './item-tracking.component.html'
})
export class ItemTrackingComponent implements OnChanges {
  @Input({ required: true }) item!: KnowledgeItem
  /** Fired after a change, so the page can reload the item */
  @Output() changed = new EventEmitter<void>()

  readonly statuses: CommitmentStatus[] = ['open', 'in_progress', 'done', 'dropped']
  readonly statusLabel = COMMITMENT_LABEL
  readonly verdicts: Verdict[] = ['achieved', 'partly', 'not_achieved']
  readonly verdictLabel = VERDICT_LABEL

  owner = ''; dueDate = ''
  expected = ''; checkBy = ''
  verdict: Verdict | '' = ''; verdictNote = ''
  editingExpectation = false
  error = ''; busy = false

  constructor(private http: HttpClient) {}

  ngOnChanges() {
    const c = this.item.commitment
    this.owner = c?.owner ?? ''; this.dueDate = c?.due_date ?? ''
    const o = this.item.outcome
    this.expected = o?.expected ?? ''; this.checkBy = o?.check_by ?? ''
    this.editingExpectation = !o?.expected
    this.verdict = ''; this.verdictNote = ''
  }

  async setStatus(status: CommitmentStatus) {
    await this.run('Could not change the status', () => firstValueFrom(this.http.put(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/status`, { status, note: '' })))
  }

  async saveCommitment() {
    await this.run('Could not save', () => firstValueFrom(this.http.put(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/commitment`,
      { owner: this.owner.trim() || null, due_date: this.dueDate || null })))
  }

  async saveExpectation() {
    await this.run('Could not save', () => firstValueFrom(this.http.put(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/outcome/expectation`,
      { expected: this.expected.trim() || null, check_by: this.checkBy || null })))
  }

  async recordVerdict(verdict: Verdict | null) {
    if (verdict === null && !confirm('Withdraw the recorded outcome?')) return
    await this.run('Could not record the outcome', () => firstValueFrom(this.http.put(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/outcome`,
      { verdict, note: verdict ? this.verdictNote : '' })))
  }

  private async run(failure: string, call: () => Promise<unknown>) {
    this.busy = true; this.error = ''
    try { await call(); this.changed.emit() }
    catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
