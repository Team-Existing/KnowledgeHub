import { Component, OnInit } from '@angular/core'
import { ActivatedRoute, Router, RouterLink } from '@angular/router'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { Artifact, CrossLink, KnowledgeItem, KnowledgeResponse, Relationship } from '../../models/api'
import { ItemLineageComponent } from '../../components/item-lineage/item-lineage.component'
import { SpaceService } from '../../services/space.service'
import { SpaceInfo } from '../../models/api'
import { IconComponent } from '../../components/icon/icon.component'

type RelatedItem = { item: KnowledgeItem; score: number }

@Component({
  selector: 'app-knowledge-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, ItemLineageComponent, IconComponent],
  templateUrl: './knowledge-detail.component.html',
  styleUrl: './knowledge-detail.component.css'
})
export class KnowledgeDetailComponent implements OnInit {
  data: KnowledgeResponse | null = null; error = ''; id = ''
  private loadedItem: KnowledgeItem | null = null
  editing = false; saving = false; deleting = false; crudError = ''
  editedTitle = ''
  editedTagsRaw = ''
  editedDetailsRaw = ''
  detailsParseError = ''
  private crossLinks: CrossLink[] = []

  // Keys to suppress: duplicates of title (what), empty steps, internal fields
  private readonly SKIP_KEYS = new Set(['what', 'steps', 'okf_original_id'])

  get item(): KnowledgeItem | undefined {
    return this.loadedItem ?? this.data?.knowledge_items.find(i => i.id === this.id)
  }
  get artifact(): Artifact | undefined {
    return this.data?.artifacts.find(a => a.id === this.item?.artifact_id)
  }
  get relationships(): Relationship[] {
    const item = this.item
    if (!this.data || !item) return []
    return this.data.relationships.filter(e => e.from === item.id || e.to === item.id)
  }

  // Deduplicated, filtered detail entries
  get cleanDetailEntries(): { key: string; value: unknown }[] {
    const details = this.item?.details || {}
    return Object.entries(details)
      .filter(([key, value]) => {
        if (this.SKIP_KEYS.has(key)) return false
        if (value === null || value === undefined || value === '') return false
        if (Array.isArray(value) && value.length === 0) return false
        // hide steps when it's a broken/empty string like '[]' or 'N/A'
        if (key === 'steps' && String(value).trim().match(/^(\[\]|n\/a|none|-)$/i)) return false
        return true
      })
      .map(([key, value]) => ({ key, value }))
  }

  get detailEntries(): { key: string; value: unknown }[] { return this.cleanDetailEntries }

  get confidence(): number | null {
    // details can be hand-edited as JSON on this page, so don't trust the declared type
    const c: unknown = this.item?.details.confidence
    if (c === null || c === undefined || c === '') return null
    const n = parseFloat(String(c))
    return isNaN(n) ? null : Math.round(n > 1 ? n : n * 100)
  }

  get confidenceClass(): string {
    const c = this.confidence
    if (c === null) return ''
    return c >= 75 ? 'conf-high' : c >= 45 ? 'conf-mid' : 'conf-low'
  }

  scoreClass(score: number): string {
    return score >= 0.75 ? 'conf-high' : score >= 0.45 ? 'conf-mid' : 'conf-low'
  }

  get relatedItems(): RelatedItem[] {
    const data = this.data, item = this.item
    if (!data || !item) return []
    return this.crossLinks
      .filter(l => l.item_id_a === item.id || l.item_id_b === item.id)
      .map(l => {
        const otherId = l.item_id_a === item.id ? l.item_id_b : l.item_id_a
        const other = data.knowledge_items.find(i => i.id === otherId)
        return other ? { item: other, score: l.score } : null
      })
      .filter((x): x is RelatedItem => x !== null)
      .sort((a, b) => b.score - a.score)
  }

  // Resolve a relationship edge to a display label + route
  resolveNode(edge: Relationship): { label: string; route: string[] } | null {
    const otherId = edge.from === this.item?.id ? edge.to : edge.from
    const ki = this.data?.knowledge_items.find(i => i.id === otherId)
    if (ki) return { label: ki.title, route: ['/knowledge', ki.id] }
    const art = this.data?.artifacts.find(a => a.id === otherId)
    if (art) return { label: art.title, route: ['/knowledge'] }
    return null
  }

  // Highlight evidence phrases from details in the source content
  get highlightedContent(): string {
    const content = this.artifact?.content
    if (!content) return ''
    const escaped = content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    const details = this.item?.details
    const phrases: string[] = []
    for (const key of ['evidence', 'context', 'rationale', 'description']) {
      const v = details?.[key]
      if (typeof v === 'string' && v.trim().length > 8) phrases.push(v.trim())
    }
    if (phrases.length === 0) return `<span style="white-space:pre-wrap">${escaped}</span>`
    let result = escaped
    for (const phrase of phrases) {
      const safe = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      try {
        result = result.replace(new RegExp(safe, 'gi'), m => `<mark>${m}</mark>`)
      } catch { /* skip malformed phrase */ }
    }
    return `<span style="white-space:pre-wrap">${result}</span>`
  }

  scrollTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // share the source artifact into another space (e.g. personal -> group)
  shareTo = ''; sharing = false; shareMessage = ''

  get otherSpaces(): SpaceInfo[] {
    return this.spaces.spaces().filter(s => s.id !== this.spaces.active()?.id)
  }

  async share() {
    const artifact = this.artifact
    if (!artifact || !this.shareTo) return
    this.sharing = true; this.shareMessage = ''
    try {
      const r = await firstValueFrom(this.http.post<{ items: number; space_name: string }>(
        `${API_BASE}/knowledge/artifacts/${encodeURIComponent(artifact.id)}/share`, { space_id: this.shareTo }))
      this.shareMessage = `Shared to ${r.space_name} with ${r.items} item${r.items === 1 ? '' : 's'}.`
      this.shareTo = ''
    } catch (e) { this.shareMessage = errorMessage(e, 'Could not share') }
    finally { this.sharing = false }
  }

  constructor(private route: ActivatedRoute, private _router: Router, private http: HttpClient,
              public spaces: SpaceService) {}

  ngOnInit() {
    // follow param changes: links between items (lineage, related) reuse this component
    this.route.paramMap.subscribe(params => {
      this.id = params.get('id') || ''
      if (!this.id) { this._router.navigate(['/knowledge']); return }
      this.loadedItem = null; this.error = ''; this.editing = false
      this.load()
    })
  }

  async load() {
    try {
      const [item, links, kr] = await Promise.all([
        firstValueFrom(this.http.get<KnowledgeItem>(`${API_BASE}/knowledge/items/${this.id}`)),
        firstValueFrom(this.http.get<CrossLink[]>(`${API_BASE}/knowledge/links`)).catch((): CrossLink[] => []),
        firstValueFrom(this.http.get<KnowledgeResponse>(`${API_BASE}/knowledge`)),
      ])
      this.loadedItem = item
      this.data = kr
      this.crossLinks = links
    } catch (e) { this.error = errorMessage(e, 'Could not load knowledge item') }
  }

  startEdit() {
    const item = this.item
    if (!item) return
    this.editedTitle = item.title
    this.editedTagsRaw = (item.tags || []).join(', ')
    this.editedDetailsRaw = JSON.stringify(item.details || {}, null, 2)
    this.detailsParseError = ''
    this.crudError = ''
    this.editing = true
  }

  async saveItem() {
    if (!this.item) return
    // Validate details JSON before sending
    let parsedDetails: Record<string, unknown>
    try {
      parsedDetails = JSON.parse(this.editedDetailsRaw || '{}')
    } catch {
      this.detailsParseError = 'Details must be valid JSON'
      return
    }
    this.detailsParseError = ''
    this.saving = true; this.crudError = ''
    try {
      const tags = this.editedTagsRaw.split(',').map(t => t.trim()).filter(Boolean)
      await firstValueFrom(this.http.put(
        `${API_BASE}/knowledge/items/${this.id}`,
        { title: this.editedTitle, tags, details: parsedDetails }
      ))
      this.editing = false
      await this.load()
    } catch (e) { this.crudError = errorMessage(e, 'Save failed') }
    finally { this.saving = false }
  }

  async deleteItem() {
    if (!confirm('Delete this knowledge item? This cannot be undone.')) return
    this.deleting = true; this.crudError = ''
    try {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/items/${this.id}`))
      window.history.back()
    } catch (e) { this.crudError = errorMessage(e, 'Delete failed') }
    finally { this.deleting = false }
  }

  humanize(v: string) { return v.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) }
  formatValue(v: unknown): string {
    if (Array.isArray(v)) return v.join(', ')
    if (typeof v === 'object' && v !== null) return JSON.stringify(v, null, 2)
    if (v === null || v === undefined || v === '') return 'Not specified'
    return String(v)
  }
}
