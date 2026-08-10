import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink, ActivatedRoute, Router } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { AuthService, API_BASE } from '../../services/auth.service'

type KnowledgeItem = { id: string; title: string; type: string; author: string; date: string; tags: string[]; review_status: string }
type Artifact = { id: string; title: string; author: string; source_type: string; created_at: string; tags: string[] }
type SearchResult = { query: string; filters: any; knowledge_items: KnowledgeItem[]; artifacts: Artifact[]; total: number }

const SOURCE_TYPES = ['manual', 'file', 'url', 'transcript', 'email', 'slack']
const ITEM_TYPES = ['decision', 'action-item', 'risk', 'best-practice', 'checklist', 'how-to', 'lesson']

@Component({
  selector: 'app-search',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="knowledge-shell">
      <section class="toolbar">
        <div>
          <h2>Search Knowledge Base</h2>
          @if (result) { <p>{{ result.total }} result{{ result.total === 1 ? '' : 's' }} for "{{ result.query || 'all' }}"</p> }
        </div>
        <button (click)="copyShareLink()">{{ copied ? 'Copied!' : 'Share link' }}</button>
      </section>

      <section class="search-controls">
        <div class="search-box">
          <input [(ngModel)]="q" (keydown.enter)="runSearch()" placeholder="Search decisions, risks, action items…" autofocus />
        </div>
        <select [(ngModel)]="typeFilter">
          <option value="">All types</option>
          @for (t of ITEM_TYPES; track t) { <option [value]="t">{{ t }}</option> }
        </select>
        <select [(ngModel)]="sourceFilter">
          <option value="">All sources</option>
          @for (s of SOURCE_TYPES; track s) { <option [value]="s">{{ s }}</option> }
        </select>
        <input [(ngModel)]="tagFilter" placeholder="Filter by tag" style="max-width:160px" />
        <button class="primary" (click)="runSearch()" [disabled]="loading">{{ loading ? 'Searching…' : 'Search' }}</button>
      </section>

      @if (result) {
        @if (result.knowledge_items.length > 0) {
          <div style="padding:0 0.25rem 0.5rem;font-size:0.85rem;color:#667085;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">
            Knowledge Items ({{ result.knowledge_items.length }})
          </div>
          <section class="knowledge-grid">
            @for (item of result.knowledge_items; track item.id) {
              <a class="knowledge-card knowledge-card-link" [routerLink]="['/knowledge', item.id]">
                <div class="card-topline">
                  <span class="type-pill">{{ item.type }}</span>
                  <span>{{ item.date | date }}</span>
                </div>
                <h3>{{ item.title }}</h3>
                <p>by {{ item.author }}</p>
                <div class="tag-row">@for (t of item.tags; track t) { <span>#{{ t }}</span> }</div>
              </a>
            }
          </section>
        }
        @if (result.artifacts.length > 0) {
          <div style="padding:0 0.25rem 0.5rem;font-size:0.85rem;color:#667085;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">
            Artifacts ({{ result.artifacts.length }})
          </div>
          <section class="knowledge-grid">
            @for (a of result.artifacts; track a.id) {
              <div class="knowledge-card">
                <div class="card-topline">
                  <span class="type-pill">{{ a.source_type }}</span>
                  <span>{{ a.created_at | date }}</span>
                </div>
                <h3>{{ a.title }}</h3>
                <p>by {{ a.author }}</p>
                <div class="tag-row">@for (t of a.tags; track t) { <span>#{{ t }}</span> }</div>
              </div>
            }
          </section>
        }
        @if (result.total === 0) {
          <div class="empty-state"><h3>No results</h3><p>Try a different query or remove a filter.</p></div>
        }
      }

      @if (!result && !loading) {
        <div class="empty-state"><h3>Start searching</h3><p>Enter a query above — results are instant and links are shareable.</p></div>
      }
    </div>
  `
})
export class SearchComponent implements OnInit {
  q = ''; typeFilter = ''; sourceFilter = ''; tagFilter = ''
  result: SearchResult | null = null; loading = false; copied = false
  readonly SOURCE_TYPES = SOURCE_TYPES; readonly ITEM_TYPES = ITEM_TYPES

  constructor(private http: HttpClient, public auth: AuthService, private route: ActivatedRoute, private router: Router) {}

  ngOnInit() {
    const p = this.route.snapshot.queryParamMap
    this.q = p.get('q') || ''; this.typeFilter = p.get('type') || ''
    this.sourceFilter = p.get('source') || ''; this.tagFilter = p.get('tag') || ''
    if (this.q || this.typeFilter || this.sourceFilter || this.tagFilter) this.runSearch()
  }

  async runSearch() {
    this.loading = true
    const qp: any = {}
    if (this.q) qp['q'] = this.q; if (this.typeFilter) qp['type'] = this.typeFilter
    if (this.sourceFilter) qp['source'] = this.sourceFilter; if (this.tagFilter) qp['tag'] = this.tagFilter
    this.router.navigate([], { queryParams: qp, replaceUrl: true })
    try {
      const apiQp: any = {}
      if (this.q) apiQp['q'] = this.q; if (this.typeFilter) apiQp['type'] = this.typeFilter
      if (this.sourceFilter) apiQp['source_type'] = this.sourceFilter; if (this.tagFilter) apiQp['tag'] = this.tagFilter
      const params = new URLSearchParams(apiQp).toString()
      this.result = await firstValueFrom(this.http.get<SearchResult>(`${API_BASE}/knowledge/search${params ? '?' + params : ''}`, { headers: this.auth.authHeaders() }))
    } catch (e: any) {
      console.error('search error:', e)
    } finally { this.loading = false }
  }

  copyShareLink() {
    const qp = new URLSearchParams()
    if (this.q) qp.set('q', this.q); if (this.typeFilter) qp.set('type', this.typeFilter)
    if (this.sourceFilter) qp.set('source', this.sourceFilter); if (this.tagFilter) qp.set('tag', this.tagFilter)
    navigator.clipboard.writeText(`${window.location.origin}/search?${qp}`)
    this.copied = true; setTimeout(() => this.copied = false, 2000)
  }
}
