import { Component, Input, OnChanges, SimpleChanges } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { ItemSystems, KnowledgeItem, SystemBrief, SystemsResponse } from '../../models/api'

/** The systems an item affects (used by impact analysis), with ones it names offered as links. */
@Component({
  selector: 'app-item-systems',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './item-systems.component.html'
})
export class ItemSystemsComponent implements OnChanges {
  @Input({ required: true }) item!: KnowledgeItem

  data: ItemSystems | null = null
  all: SystemBrief[] = []
  adding = ''
  error = ''; busy = false

  constructor(private http: HttpClient) {}

  async ngOnChanges(changes: SimpleChanges) {
    const c = changes['item']
    if (c && c.previousValue?.id !== c.currentValue?.id) await this.load()
  }

  get addable(): SystemBrief[] {
    const linked = new Set(this.data?.linked.map(s => s.id))
    return this.all.filter(s => !linked.has(s.id))
  }

  async load() {
    this.error = ''
    try {
      const id = encodeURIComponent(this.item.id)
      const [data, systems] = await Promise.all([
        firstValueFrom(this.http.get<ItemSystems>(`${API_BASE}/knowledge/items/${id}/systems`)),
        firstValueFrom(this.http.get<SystemsResponse>(`${API_BASE}/knowledge/systems`)),
      ])
      this.data = data
      this.all = systems.systems.map(s => ({ id: s.id, name: s.name, kind: s.kind }))
    } catch (e) { this.error = errorMessage(e, 'Could not load systems') }
  }

  async link(systemId: string) {
    if (!systemId) return
    await this.run(() => firstValueFrom(this.http.post(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/systems`, { system_id: systemId })))
    this.adding = ''
  }

  async unlink(s: SystemBrief) {
    await this.run(() => firstValueFrom(this.http.delete(
      `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/systems/${encodeURIComponent(s.id)}`)))
  }

  private async run(call: () => Promise<unknown>) {
    this.busy = true; this.error = ''
    try { await call(); await this.load() }
    catch (e) { this.error = errorMessage(e, 'Could not change the link') }
    finally { this.busy = false }
  }
}
