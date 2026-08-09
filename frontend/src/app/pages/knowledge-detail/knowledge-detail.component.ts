import { Component, OnInit } from '@angular/core'
import { ActivatedRoute, Router, RouterLink } from '@angular/router'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { AuthService, API_BASE } from '../../services/auth.service'

type Artifact = { id: string; title: string; content: string; source: string; author: string; tags: string[]; created_at: string }
type KnowledgeItem = { id: string; artifact_id: string; title: string; type: string; author: string; date: string; tags: string[]; details: Record<string, unknown> }
type Relationship = { from: string; to: string; type: string }
type CrossLink = { item_id_a: string; item_id_b: string; score: number }
type RelatedItem = { item: KnowledgeItem; score: number }

@Component({
  selector: 'app-knowledge-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="detail-shell">
      <a class="back-link" routerLink="/knowledge">← Back to hub</a>

      <ng-container *ngIf="error">
        <section class="empty-state"><h3>Could not load this item</h3><p>{{ error }}</p></section>
      </ng-container>

      <ng-container *ngIf="!error && !item">
        <section class="empty-state"><h3>{{ data ? 'Knowledge item not found' : 'Loading…' }}</h3></section>
      </ng-container>

      <ng-container *ngIf="!error && item">
        <section class="detail-hero">
          <div class="detail-title">
            <span class="type-pill">{{ item.type }}</span>
            <ng-container *ngIf="!editing; else editTitle">
              <h2>{{ item.title }}</h2>
            </ng-container>
            <ng-template #editTitle>
              <input [(ngModel)]="editedTitle" style="font-size:1.4rem;font-weight:600;width:100%" />
            </ng-template>
          </div>

          <!-- Header chips: skip unknown author, make tags + relationships clickable -->
          <div class="hero-chips">
            <span *ngIf="item.author && item.author !== 'unknown'" class="chip chip-neutral">✍ {{ item.author }}</span>
            <span class="chip chip-neutral">🗓 {{ item.date | date:'mediumDate' }}</span>
            <span *ngIf="relationships.length > 0" class="chip chip-link" (click)="scrollTo('rels')">🔗 {{ relationships.length }} relationship{{ relationships.length === 1 ? '' : 's' }}</span>
            <a *ngFor="let tag of item.tags" class="chip chip-tag" [routerLink]="['/search']" [queryParams]="{tag: tag}">#{{ tag }}</a>
            <span *ngIf="item.tags.length === 0" class="chip chip-action" (click)="startEdit()">+ Add tags</span>
            <span *ngIf="confidence !== null" class="chip" [ngClass]="confidenceClass">{{ confidence }}% confidence</span>
          </div>

          <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
            <ng-container *ngIf="!editing">
              <button (click)="startEdit()">Edit</button>
              <button class="danger" (click)="deleteItem()" [disabled]="deleting">{{ deleting ? 'Deleting…' : 'Delete item' }}</button>
            </ng-container>
            <ng-container *ngIf="editing">
              <button class="primary" (click)="saveItem()" [disabled]="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
              <button (click)="editing = false">Cancel</button>
            </ng-container>
            <span *ngIf="crudError" class="error-text">{{ crudError }}</span>
          </div>
        </section>

        <!-- Edit form -->
        <section *ngIf="editing" class="detail-panel">
          <h3>Edit Item</h3>
          <div class="detail-edit-grid">
            <div class="form-row">
              <label>Tags <span class="muted-text">(comma-separated)</span></label>
              <input [(ngModel)]="editedTagsRaw" placeholder="tag1, tag2" />
            </div>
            <div class="form-row">
              <label>Details <span class="muted-text">(JSON)</span></label>
              <textarea [(ngModel)]="editedDetailsRaw" rows="6" style="font-family:monospace;font-size:0.8rem"></textarea>
              <span *ngIf="detailsParseError" class="error-text">{{ detailsParseError }}</span>
            </div>
          </div>
        </section>

        <div class="detail-grid">
          <!-- Left: extracted details, deduplicated, steps hidden when empty -->
          <section class="detail-panel">
            <h3>Extracted Details</h3>
            <p *ngIf="cleanDetailEntries.length === 0" class="muted-text">No structured detail was captured.</p>
            <dl *ngIf="cleanDetailEntries.length > 0" class="detail-fields">
              <div *ngFor="let entry of cleanDetailEntries">
                <dt>
                  {{ humanize(entry.key) }}
                  <span *ngIf="entry.key === 'confidence'" class="conf-bar-wrap">
                    <span class="conf-bar" [style.width]="entry.value + '%'" [ngClass]="confidenceClass"></span>
                  </span>
                </dt>
                <dd *ngIf="entry.key !== 'confidence'">{{ formatValue(entry.value) }}</dd>
                <dd *ngIf="entry.key === 'confidence'" [ngClass]="confidenceClass" style="font-weight:600">{{ entry.value }}%</dd>
              </div>
            </dl>
          </section>

          <!-- Right: source artifact + related items -->
          <aside class="detail-panel">
            <h3>Source Artifact</h3>
            <ng-container *ngIf="artifact; else noArtifact">
              <div class="artifact-summary">
                <div class="artifact-icon">📄</div>
                <div>
                  <h4>{{ artifact.title }}</h4>
                  <p *ngIf="artifact.author && artifact.author !== 'unknown'">by {{ artifact.author }}</p>
                  <p>{{ artifact.source }}</p>
                </div>
              </div>
            </ng-container>
            <ng-template #noArtifact>
              <p class="muted-text">No source artifact found.</p>
            </ng-template>

            <!-- Related items moved here to fill the space -->
            <ng-container *ngIf="relatedItems.length > 0">
              <h4 style="margin:1.25rem 0 0.6rem;font-size:0.875rem;color:#344054">Related Items</h4>
              <div class="related-list">
                <a *ngFor="let r of relatedItems" class="related-row" [routerLink]="['/knowledge', r.item.id]">
                  <span class="type-pill" style="font-size:0.7rem">{{ r.item.type }}</span>
                  <span class="related-title">{{ r.item.title }}</span>
                  <span class="conf-badge" [ngClass]="scoreClass(r.score)">{{ (r.score * 100).toFixed(0) }}%</span>
                </a>
              </div>
            </ng-container>
          </aside>
        </div>

        <!-- Relationships: resolved name + link instead of raw ID -->
        <section id="rels" class="detail-panel">
          <h3>Relationships</h3>
          <ng-container *ngIf="relationships.length > 0; else noRels">
            <div class="relationship-table">
              <div *ngFor="let edge of relationships">
                <span>{{ edge.from === item.id ? 'Outgoing' : 'Incoming' }}</span>
                <strong>{{ edge.type }}</strong>
                <a *ngIf="resolveNode(edge) as node" [routerLink]="node.route" class="rel-name-link">{{ node.label }}</a>
                <span *ngIf="!resolveNode(edge)" class="muted-text" style="font-size:0.8rem">{{ edge.from === item.id ? edge.to : edge.from }}</span>
              </div>
            </div>
          </ng-container>
          <ng-template #noRels>
            <p class="muted-text">No relationships recorded for this item yet.</p>
          </ng-template>
        </section>

        <!-- Source preview with evidence highlighting -->
        <section *ngIf="artifact" class="detail-panel">
          <h3>Source Preview</h3>
          <div class="source-preview" [innerHTML]="highlightedContent"></div>
        </section>
      </ng-container>
    </div>
  `,
  styles: [`
    .detail-edit-grid { display: flex; flex-direction: column; gap: 1rem; }
    .form-row { display: flex; flex-direction: column; gap: 0.35rem; }
    .form-row label { font-size: 0.85rem; font-weight: 600; color: #344054; }
    .form-row input, .form-row textarea {
      border: 1px solid #ddd; border-radius: 6px;
      font-family: inherit; font-size: 0.875rem; padding: 0.6rem 0.75rem;
    }
    .form-row input:focus, .form-row textarea:focus {
      outline: none; border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102,126,234,0.15);
    }
    .hero-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.25rem 0; }
    .chip {
      display: inline-flex; align-items: center; gap: 0.3rem;
      border-radius: 999px; font-size: 0.78rem; padding: 0.25rem 0.65rem;
      font-weight: 500; white-space: nowrap;
    }
    .chip-neutral { background: #f2f4f7; color: #475467; }
    .chip-tag { background: #ede9fe; color: #5b21b6; text-decoration: none; cursor: pointer; }
    .chip-tag:hover { background: #ddd6fe; }
    .chip-link { background: #e0f2fe; color: #0369a1; cursor: pointer; }
    .chip-link:hover { background: #bae6fd; }
    .chip-action { background: #f0fdf4; color: #15803d; cursor: pointer; border: 1px dashed #86efac; }
    .chip-action:hover { background: #dcfce7; }
    .conf-high { background: #dcfce7; color: #15803d; }
    .conf-mid  { background: #fef9c3; color: #854d0e; }
    .conf-low  { background: #fee2e2; color: #991b1b; }
    .conf-bar-wrap { display:inline-block; width:80px; height:6px; background:#e5e7eb; border-radius:3px; vertical-align:middle; margin-left:0.5rem; overflow:hidden; }
    .conf-bar { display:block; height:100%; border-radius:3px; transition:width 0.3s; }
    .conf-bar.conf-high { background:#22c55e; }
    .conf-bar.conf-mid  { background:#eab308; }
    .conf-bar.conf-low  { background:#ef4444; }
    .related-list { display: flex; flex-direction: column; gap: 0.4rem; }
    .related-row {
      display: flex; align-items: center; gap: 0.5rem;
      padding: 0.45rem 0.6rem; border-radius: 6px;
      background: #f8fbfa; border: 1px solid #e5ecea;
      text-decoration: none; color: inherit;
      transition: border-color 0.15s;
    }
    .related-row:hover { border-color: #667eea; }
    .related-title { flex: 1; font-size: 0.82rem; font-weight: 500; color: #1f2933; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .conf-badge { font-size: 0.72rem; font-weight: 600; padding: 0.15rem 0.4rem; border-radius: 4px; flex-shrink: 0; }
    .rel-name-link { color: #667eea; text-decoration: none; font-size: 0.875rem; font-weight: 500; }
    .rel-name-link:hover { text-decoration: underline; }
    :host ::ng-deep .source-preview mark { background: #fef08a; border-radius: 2px; padding: 0 2px; }
    .source-preview { color: #344054; line-height: 1.6; max-height: 360px; overflow: auto; white-space: pre-wrap; font-size: 0.875rem; }
  `]
})
export class KnowledgeDetailComponent implements OnInit {
  data: any = null; error = ''; id = ''
  editing = false; saving = false; deleting = false; crudError = ''
  editedTitle = ''
  editedTagsRaw = ''
  editedDetailsRaw = ''
  detailsParseError = ''
  private crossLinks: CrossLink[] = []

  // Keys to suppress: duplicates of title (what), empty steps, internal fields
  private readonly SKIP_KEYS = new Set(['what', 'steps', 'okf_original_id'])

  get item(): KnowledgeItem | undefined {
    return this.data?._item || this.data?.knowledge_items?.find((i: KnowledgeItem) => i.id === this.id)
  }
  get artifact(): Artifact | undefined {
    return this.data?.artifacts?.find((a: Artifact) => a.id === this.item?.artifact_id)
  }
  get relationships(): Relationship[] {
    if (!this.data || !this.item) return []
    return this.data.relationships.filter((e: Relationship) => e.from === this.item!.id || e.to === this.item!.id)
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
    const c = (this.item?.details as any)?.confidence
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
    if (!this.data || !this.item) return []
    const item = this.item
    return this.crossLinks
      .filter(l => l.item_id_a === item.id || l.item_id_b === item.id)
      .map(l => {
        const otherId = l.item_id_a === item.id ? l.item_id_b : l.item_id_a
        const other: KnowledgeItem | undefined = this.data.knowledge_items.find((i: KnowledgeItem) => i.id === otherId)
        return other ? { item: other, score: l.score } : null
      })
      .filter((x): x is RelatedItem => x !== null)
      .sort((a, b) => b.score - a.score)
  }

  // Resolve a relationship edge to a display label + route
  resolveNode(edge: Relationship): { label: string; route: any[] } | null {
    const otherId = edge.from === this.item?.id ? edge.to : edge.from
    const ki = this.data?.knowledge_items?.find((i: KnowledgeItem) => i.id === otherId)
    if (ki) return { label: ki.title, route: ['/knowledge', ki.id] }
    const art = this.data?.artifacts?.find((a: Artifact) => a.id === otherId)
    if (art) return { label: art.title, route: ['/knowledge'] }
    return null
  }

  // Highlight evidence phrases from details in the source content
  get highlightedContent(): string {
    const content = this.artifact?.content
    if (!content) return ''
    const escaped = content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    const details = this.item?.details as any
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

  constructor(private route: ActivatedRoute, private _router: Router, private http: HttpClient, public auth: AuthService) {}

  ngOnInit() {
    this.id = this.route.snapshot.paramMap.get('id') || ''
    if (!this.id) { this._router.navigate(['/knowledge']); return }
    this.load()
  }

  async load() {
    try {
      const headers = this.auth.authHeaders()
      const [item, lr, kr]: any[] = await Promise.all([
        firstValueFrom(this.http.get(`${API_BASE}/knowledge/items/${this.id}`, { headers })),
        firstValueFrom(this.http.get(`${API_BASE}/knowledge/links`, { headers })).catch(() => []),
        firstValueFrom(this.http.get(`${API_BASE}/knowledge`, { headers })),
      ])
      this.data = { ...kr, _item: item }
      this.crossLinks = lr || []
    } catch (e: any) { this.error = e?.message || 'Could not load knowledge item' }
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
        { title: this.editedTitle, tags, details: parsedDetails },
        { headers: this.auth.authHeaders() }
      ))
      this.editing = false
      await this.load()
    } catch (e: any) { this.crudError = e?.message || 'Save failed' }
    finally { this.saving = false }
  }

  async deleteItem() {
    if (!confirm('Delete this knowledge item? This cannot be undone.')) return
    this.deleting = true; this.crudError = ''
    try {
      await firstValueFrom(this.http.delete(
        `${API_BASE}/knowledge/items/${this.id}`,
        { headers: this.auth.authHeaders() }
      ))
      window.history.back()
    } catch (e: any) { this.crudError = e?.message || 'Delete failed' }
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
