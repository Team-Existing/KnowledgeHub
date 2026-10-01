import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient, HttpParams } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { KnowledgeItem, LineageEdge, RegisterResponse } from '../../models/api'

type Register = 'decision' | 'risk'
interface Ref { id: string; title: string; kind: string }
interface Row { item: KnowledgeItem; replacedBy: Ref[]; replaces: Ref[]; addressedBy: Ref[] }

const STATUS_ORDER: Record<Register, string[]> = {
  decision: ['active', 'proposed', 'superseded', 'reversed', 'deprecated', 'rejected'],
  risk: ['open', 'materialized', 'mitigated', 'closed'],
}

@Component({
  selector: 'app-decisions',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './decisions.component.html',
})
export class DecisionsComponent implements OnInit {
  register: Register = 'decision'
  statusFilter = ''
  query = ''
  rows: Row[] = []
  error = ''; loading = false

  constructor(private http: HttpClient) {}

  ngOnInit() { this.load() }

  async switchTo(register: Register) {
    if (register === this.register) return
    this.register = register; this.statusFilter = ''
    await this.load()
  }

  async load() {
    this.loading = true; this.error = ''
    try {
      const params = new HttpParams().set('type', this.register)
      const data = await firstValueFrom(this.http.get<RegisterResponse>(`${API_BASE}/knowledge/register`, { params }))
      this.rows = this.toRows(data)
    } catch (e) { this.error = errorMessage(e, 'Could not load the register') }
    finally { this.loading = false }
  }

  private toRows(data: RegisterResponse): Row[] {
    const titles = new Map(data.items.map(i => [i.id, i.title]))
    const ref = (id: string, kind: string): Ref => ({ id, title: titles.get(id) ?? id, kind })
    const byTarget = new Map<string, LineageEdge[]>()
    const bySource = new Map<string, LineageEdge[]>()
    for (const e of data.edges) {
      byTarget.set(e.to, [...(byTarget.get(e.to) ?? []), e])
      bySource.set(e.from, [...(bySource.get(e.from) ?? []), e])
    }
    const chain = (k: string) => k === 'supersedes' || k === 'reverses' || k === 'amends'
    return data.items
      .map(item => ({
        item,
        replacedBy: (byTarget.get(item.id) ?? []).filter(e => chain(e.kind) && titles.has(e.from)).map(e => ref(e.from, e.kind)),
        replaces: (bySource.get(item.id) ?? []).filter(e => chain(e.kind) && titles.has(e.to)).map(e => ref(e.to, e.kind)),
        // for risks: what realized or mitigated them (sources may be lessons/actions, outside this register)
        addressedBy: (byTarget.get(item.id) ?? []).filter(e => !chain(e.kind)).map(e => ref(e.from, e.kind)),
      }))
      .sort((a, b) => (b.item.date || '').localeCompare(a.item.date || ''))
  }

  get statuses(): { status: string; count: number }[] {
    const counts = new Map<string, number>()
    for (const r of this.rows) counts.set(r.item.status ?? '', (counts.get(r.item.status ?? '') ?? 0) + 1)
    return STATUS_ORDER[this.register].filter(s => counts.has(s)).map(s => ({ status: s, count: counts.get(s)! }))
  }

  get visible(): Row[] {
    const q = this.query.trim().toLowerCase()
    return this.rows.filter(r =>
      (!this.statusFilter || r.item.status === this.statusFilter) &&
      (!q || r.item.title.toLowerCase().includes(q) || r.item.tags.some(t => t.toLowerCase().includes(q))))
  }

  verb(kind: string): string {
    return ({ supersedes: 'superseded', reverses: 'reversed', amends: 'amended',
              realizes: 'happened', mitigates: 'mitigated' } as Record<string, string>)[kind] ?? kind
  }
}
