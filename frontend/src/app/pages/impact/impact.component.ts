import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { ActivatedRoute, RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { VERDICT_LABEL } from '../../services/tracking'
import { ImpactDecision, ImpactRelation, ImpactReport, SystemInfo, SystemsResponse } from '../../models/api'

const CURRENT = ['active', 'proposed']

const RELATION_LABEL: Record<ImpactRelation, string> = {
  conflicts: 'Conflicts', changes: 'Changes it', consistent: 'Consistent', unrelated: 'Unrelated',
}

/**
 * Before changing course: describe the change (or start from a decision or a
 * system) and see what the record says it touches. Read-only.
 */
@Component({
  selector: 'app-impact',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './impact.component.html'
})
export class ImpactComponent implements OnInit {
  proposal = ''
  systems: SystemInfo[] = []
  chosen = new Set<string>()
  decisionId: string | null = null
  report: ImpactReport | null = null
  error = ''; busy = false

  readonly relationLabel = RELATION_LABEL
  readonly verdictLabel = VERDICT_LABEL

  constructor(private http: HttpClient, private route: ActivatedRoute) {}

  async ngOnInit() {
    const params = this.route.snapshot.queryParamMap
    this.decisionId = params.get('decision')
    const system = params.get('system')
    if (system) this.chosen.add(system)
    try {
      this.systems = (await firstValueFrom(this.http.get<SystemsResponse>(`${API_BASE}/knowledge/systems`))).systems
    } catch { /* systems are optional */ }
    if (this.decisionId || system) await this.analyze()
  }

  toggle(id: string) { if (this.chosen.has(id)) this.chosen.delete(id); else this.chosen.add(id) }

  get canRun(): boolean { return !!this.proposal.trim() || this.chosen.size > 0 || !!this.decisionId }

  /** Decisions still in force (the backend's CURRENT); the rest is history */
  get current(): ImpactDecision[] { return this.report?.decisions.filter(d => CURRENT.includes(d.status ?? '')) ?? [] }
  get history(): ImpactDecision[] { return this.report?.decisions.filter(d => !CURRENT.includes(d.status ?? '')) ?? [] }

  alternatives(d: ImpactDecision): string {
    return Array.isArray(d.alternatives) ? d.alternatives.join('; ') : d.alternatives ?? ''
  }

  clearDecision() { this.decisionId = null; this.report = null }

  async analyze() {
    this.busy = true; this.error = ''
    try {
      this.report = await firstValueFrom(this.http.post<ImpactReport>(`${API_BASE}/knowledge/impact`, {
        proposal: this.proposal, system_ids: [...this.chosen], decision_id: this.decisionId,
      }))
    } catch (e) { this.error = errorMessage(e, 'Could not analyze the change') }
    finally { this.busy = false }
  }
}
