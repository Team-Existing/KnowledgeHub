import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { DependencySuggestion, SystemInfo, SystemKind, SystemsResponse } from '../../models/api'

/**
 * The space's systems (services, databases, teams …) and what depends on what.
 * Impact analysis follows these dependencies to see what a change would reach.
 */
@Component({
  selector: 'app-systems',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './systems.component.html'
})
export class SystemsComponent implements OnInit {
  systems: SystemInfo[] = []
  /** dependencies the knowledge states that aren't recorded yet */
  suggestions: DependencySuggestion[] = []
  kinds: SystemKind[] = []
  selected: SystemInfo | null = null

  // new system
  name = ''; kind: SystemKind = 'service'
  // editing the selected one
  edit = { name: '', kind: 'service' as SystemKind, owner: '', aliases: '', description: '' }
  dependsOn = ''
  error = ''; notice = ''; busy = false

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.load() }

  nameOf(id: string): string { return this.systems.find(s => s.id === id)?.name ?? id }

  dependentNames(): string { return (this.selected?.dependents ?? []).map(id => this.nameOf(id)).join(', ') }

  get candidates(): SystemInfo[] {
    const s = this.selected
    return s ? this.systems.filter(x => x.id !== s.id && !s.depends_on.includes(x.id)) : []
  }

  async load() {
    try {
      const body = await firstValueFrom(this.http.get<SystemsResponse>(`${API_BASE}/knowledge/systems`))
      this.systems = body.systems; this.kinds = body.kinds
      this.suggestions = await firstValueFrom(this.http.get<DependencySuggestion[]>(`${API_BASE}/knowledge/systems/suggestions`))
      if (this.selected) {
        const again = this.systems.find(s => s.id === this.selected!.id)
        if (again) this.select(again)
        else this.selected = null
      }
    } catch (e) { this.error = errorMessage(e, 'Could not load systems') }
  }

  select(s: SystemInfo) {
    this.selected = s; this.dependsOn = ''
    this.edit = { name: s.name, kind: s.kind, owner: s.owner ?? '', aliases: s.aliases.join(', '), description: s.description ?? '' }
  }

  async create() {
    await this.run(async () => {
      const s = await firstValueFrom(this.http.post<SystemInfo>(`${API_BASE}/knowledge/systems`, { name: this.name, kind: this.kind }))
      this.name = ''
      await this.load()
      const created = this.systems.find(x => x.id === s.id)
      if (created) this.select(created)
    }, 'Could not add the system')
  }

  async save() {
    await this.run(async () => {
      await firstValueFrom(this.http.patch(`${API_BASE}/knowledge/systems/${encodeURIComponent(this.selected!.id)}`, {
        name: this.edit.name, kind: this.edit.kind, owner: this.edit.owner || null, description: this.edit.description || null,
        aliases: this.edit.aliases.split(',').map(a => a.trim()).filter(Boolean),
      }))
      this.notice = 'Saved.'
      await this.load()
    }, 'Could not save')
  }

  async addDependency() {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/systems/${encodeURIComponent(this.selected!.id)}/dependencies`,
        { depends_on: this.dependsOn }))
      await this.load()
    }, 'Could not add the dependency')
  }

  async acceptSuggestion(s: DependencySuggestion) {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/systems/${encodeURIComponent(s.from)}/dependencies`,
        { depends_on: s.to, note: `From: ${s.evidence[0]?.title ?? 'the knowledge base'}`.slice(0, 500) }))
      await this.load()
    }, 'Could not add the dependency')
  }

  async removeDependency(otherId: string) {
    await this.run(async () => {
      await firstValueFrom(this.http.delete(
        `${API_BASE}/knowledge/systems/${encodeURIComponent(this.selected!.id)}/dependencies/${encodeURIComponent(otherId)}`))
      await this.load()
    }, 'Could not remove the dependency')
  }

  async remove() {
    if (!confirm(`Delete ${this.selected!.name}? Its dependencies and item links go with it; the items stay.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/systems/${encodeURIComponent(this.selected!.id)}`))
      this.selected = null
      await this.load()
    }, 'Could not delete the system')
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''; this.notice = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
