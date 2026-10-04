import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import {
  ItemEvent, KnowledgeItem, LineageLink, LineageResponse, LinkKind, LinkKindsResponse, LinkSuggestion,
} from '../../models/api'

type Direction = 'out' | 'in'
interface LinkOption { key: string; kind: LinkKind; direction: Direction; label: string; otherTypes: string[] | null }

/** How a link reads from the current item's side: out = "this <verb> other", in = "this <passive> other". */
const PHRASES: Record<LinkKind, { out: string; in: string }> = {
  supersedes: { out: 'Supersedes', in: 'Superseded by' },
  reverses: { out: 'Reverses', in: 'Reversed by' },
  amends: { out: 'Amends', in: 'Amended by' },
  realizes: { out: 'Shows this risk happened:', in: 'Happened — shown by' },
  mitigates: { out: 'Mitigates', in: 'Mitigated by' },
  learned_from: { out: 'Learned from', in: 'Taught the lesson' },
}

@Component({
  selector: 'app-item-lineage',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './item-lineage.component.html'
})
export class ItemLineageComponent implements OnChanges {
  @Input({ required: true }) item!: KnowledgeItem
  /** All of the user's items, for picking a link target */
  @Input() items: KnowledgeItem[] = []
  /** Fired after anything that may change statuses, so the page can reload */
  @Output() changed = new EventEmitter<void>()

  lineage: LineageResponse | null = null
  suggestions: LinkSuggestion[] = []
  options: LinkOption[] = []
  error = ''; busy = false

  // add-link form
  optionKey = ''; targetId = ''; note = ''
  // declared status form
  declared = ''; statusNote = ''

  private kinds: LinkKindsResponse | null = null

  constructor(private http: HttpClient) {}

  async ngOnChanges(changes: SimpleChanges) {
    // the page re-fetches the item after `changed`; only a different item needs a reload here
    const c = changes['item']
    if (c && c.previousValue?.id !== c.currentValue?.id) await this.load()
  }

  async load() {
    this.error = ''
    try {
      const id = encodeURIComponent(this.item.id)
      const [lineage, suggestions, kinds] = await Promise.all([
        firstValueFrom(this.http.get<LineageResponse>(`${API_BASE}/knowledge/items/${id}/lineage`)),
        firstValueFrom(this.http.get<LinkSuggestion[]>(`${API_BASE}/knowledge/items/${id}/lineage/suggestions`))
          .catch((): LinkSuggestion[] => []),
        this.kinds ? Promise.resolve(this.kinds)
          : firstValueFrom(this.http.get<LinkKindsResponse>(`${API_BASE}/knowledge/lineage/kinds`)),
      ])
      this.lineage = lineage; this.suggestions = suggestions; this.kinds = kinds
      this.declared = lineage.declared_status ?? ''
      this.options = this.buildOptions()
    } catch (e) { this.error = errorMessage(e, 'Could not load history') }
  }

  private buildOptions(): LinkOption[] {
    const type = this.item.type
    const out: LinkOption[] = []
    for (const k of this.kinds?.kinds ?? []) {
      if (!k.from_types || k.from_types.includes(type)) {
        out.push({ key: `${k.kind}:out`, kind: k.kind, direction: 'out', label: `${PHRASES[k.kind].out} …`, otherTypes: k.to_types })
      }
      if (!k.to_types || k.to_types.includes(type)) {
        out.push({ key: `${k.kind}:in`, kind: k.kind, direction: 'in', label: `${PHRASES[k.kind].in} …`, otherTypes: k.from_types })
      }
    }
    return out
  }

  get selectedOption(): LinkOption | undefined { return this.options.find(o => o.key === this.optionKey) }

  get targetCandidates(): KnowledgeItem[] {
    const opt = this.selectedOption
    if (!opt) return []
    return this.items
      .filter(i => i.id !== this.item.id && i.review_status !== 'rejected')
      .filter(i => !opt.otherTypes || opt.otherTypes.includes(i.type))
      .sort((a, b) => (b.date || '').localeCompare(a.date || ''))
  }

  get hasStatus(): boolean { return (this.lineage?.declarable_statuses.length ?? 0) > 0 }

  phrase(link: LineageLink, direction: Direction): string { return PHRASES[link.kind]?.[direction] ?? link.kind }

  suggestionPhrase(s: LinkSuggestion): string {
    return PHRASES[s.kind]?.[s.from === this.item.id ? 'out' : 'in'] ?? s.kind
  }

  async addLink() {
    const opt = this.selectedOption
    if (!opt || !this.targetId) return
    await this.post(opt.kind, this.targetId, opt.direction, this.note)
    this.optionKey = ''; this.targetId = ''; this.note = ''
  }

  async acceptSuggestion(s: LinkSuggestion) {
    const direction: Direction = s.from === this.item.id ? 'out' : 'in'
    await this.post(s.kind, s.item.id, direction, 'accepted suggestion')
  }

  private async post(kind: LinkKind, targetId: string, direction: Direction, note: string) {
    this.busy = true; this.error = ''
    try {
      await firstValueFrom(this.http.post(
        `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/lineage`,
        { target_id: targetId, kind, direction, note },
      ))
      this.changed.emit()
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not add link') }
    finally { this.busy = false }
  }

  async removeLink(otherId: string) {
    if (!confirm('Remove this link? Statuses that depended on it are recalculated.')) return
    this.busy = true; this.error = ''
    try {
      await firstValueFrom(this.http.delete(
        `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/lineage/${encodeURIComponent(otherId)}`))
      this.changed.emit()
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not remove link') }
    finally { this.busy = false }
  }

  async saveStatus() {
    this.busy = true; this.error = ''
    try {
      await firstValueFrom(this.http.put(
        `${API_BASE}/knowledge/items/${encodeURIComponent(this.item.id)}/status`,
        { status: this.declared || null, note: this.statusNote },
      ))
      this.statusNote = ''
      this.changed.emit()
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not set status') }
    finally { this.busy = false }
  }

  describe(e: ItemEvent): string {
    const d = e.detail || {}
    switch (e.kind) {
      case 'reviewed': return `Review: ${d['from'] ?? 'pending'} → ${d['to']}${d['note'] ? ` (“${d['note']}”)` : ''}`
      case 'edited': return `Edited ${(d['fields'] || []).join(', ')}`
      case 'status_changed': return `Status: ${d['from']} → ${d['to']}`
      case 'status_declared': return d['status'] ? `Status declared as ${d['status']}${d['note'] ? ` (“${d['note']}”)` : ''}` : 'Declared status cleared'
      case 'linked': {
        const p = PHRASES[d['kind'] as LinkKind]
        return `${p ? p[d['direction'] as Direction] : d['kind']} “${d['title'] ?? d['item_id']}”`
      }
      case 'unlinked': return `Removed ${d['kind']} link`
      default: return e.kind
    }
  }
}
