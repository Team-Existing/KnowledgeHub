import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { RouterLink } from '@angular/router'
import { HttpClient, HttpParams } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { VERDICT_LABEL } from '../../services/tracking'
import { Abandoned, Controversial, KnowledgeMap, Mistakes } from '../../models/api'

type Tab = 'map' | 'abandoned' | 'mistakes' | 'controversial' | 'ask'
const SYSTEMS_KEY = 'kh_learn_systems'

/**
 * Institutional learning: what to understand before working on a system, what
 * was tried and abandoned, recurring mistakes, and controversial decisions.
 * Built from the record (backend services/learning.py); questions go to GraphRAG.
 */
@Component({
  selector: 'app-learn',
  imports: [CommonModule, RouterLink],
  templateUrl: './learn.component.html'
})
export class LearnComponent implements OnInit {
  tab: Tab = 'map'
  map: KnowledgeMap | null = null
  abandoned: Abandoned | null = null
  mistakes: Mistakes | null = null
  controversial: Controversial | null = null
  /** the systems "my map" covers; remembered in this browser */
  chosen = new Set<string>(readChosen())
  error = ''; loading = false

  readonly verdictLabel = VERDICT_LABEL
  readonly tabs: { key: Tab; label: string }[] = [
    { key: 'map', label: 'Knowledge map' }, { key: 'abandoned', label: 'Tried and abandoned' },
    { key: 'mistakes', label: 'Recurring mistakes' }, { key: 'controversial', label: 'Controversial' },
    { key: 'ask', label: 'Ask' },
  ]

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.loadMap() }

  /** Questions a newcomer asks, ready to send to GraphRAG */
  get questions(): string[] {
    const names = (this.map?.all_systems ?? []).map(s => s.name)
    const first = names[0] ?? 'our payment service'
    return [
      `Why does ${first} look the way it does?`,
      'Which approaches have we tried and abandoned, and why?',
      'What are our most common production mistakes?',
      ...names.slice(0, 3).map(n => `What should I understand before changing ${n}?`),
      'Which architectural decisions are controversial?',
    ]
  }

  async show(tab: Tab) {
    this.tab = tab; this.error = ''
    try {
      this.loading = true
      if (tab === 'abandoned' && !this.abandoned) this.abandoned = await firstValueFrom(this.http.get<Abandoned>(`${API_BASE}/knowledge/learning/abandoned`))
      if (tab === 'mistakes' && !this.mistakes) this.mistakes = await firstValueFrom(this.http.get<Mistakes>(`${API_BASE}/knowledge/learning/mistakes`))
      if (tab === 'controversial' && !this.controversial) this.controversial = await firstValueFrom(this.http.get<Controversial>(`${API_BASE}/knowledge/learning/controversial`))
    } catch (e) { this.error = errorMessage(e, 'Could not load this') }
    finally { this.loading = false }
  }

  async loadMap() {
    this.loading = true; this.error = ''
    try {
      let params = new HttpParams()
      for (const id of this.chosen) params = params.append('system', id)
      this.map = await firstValueFrom(this.http.get<KnowledgeMap>(`${API_BASE}/knowledge/learning/map`, { params }))
      // systems removed since they were chosen drop out
      const known = new Set(this.map.all_systems.map(s => s.id))
      for (const id of [...this.chosen]) if (!known.has(id)) this.chosen.delete(id)
    } catch (e) { this.error = errorMessage(e, 'Could not load the knowledge map') }
    finally { this.loading = false }
  }

  async toggle(id: string) {
    if (this.chosen.has(id)) this.chosen.delete(id); else this.chosen.add(id)
    try { localStorage.setItem(SYSTEMS_KEY, JSON.stringify([...this.chosen])) } catch { /* not remembered */ }
    await this.loadMap()
  }
}

function readChosen(): string[] {
  try { return JSON.parse(localStorage.getItem(SYSTEMS_KEY) || '[]') } catch { return [] }
}
