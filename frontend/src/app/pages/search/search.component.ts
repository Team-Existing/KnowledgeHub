import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink, ActivatedRoute, Router } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { SearchResult } from '../../models/api'

const SOURCE_TYPES = ['manual', 'file', 'url', 'transcript', 'email', 'slack']
const ITEM_TYPES = ['decision', 'action-item', 'risk', 'best-practice', 'checklist', 'how-to', 'lesson']

@Component({
  selector: 'app-search',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './search.component.html'
})
export class SearchComponent implements OnInit {
  q = ''; typeFilter = ''; sourceFilter = ''; tagFilter = ''
  result: SearchResult | null = null; loading = false; copied = false; error = ''
  readonly SOURCE_TYPES = SOURCE_TYPES; readonly ITEM_TYPES = ITEM_TYPES

  constructor(private http: HttpClient, private route: ActivatedRoute, private router: Router) {}

  ngOnInit() {
    const p = this.route.snapshot.queryParamMap
    this.q = p.get('q') || ''; this.typeFilter = p.get('type') || ''
    this.sourceFilter = p.get('source') || ''; this.tagFilter = p.get('tag') || ''
    if (this.q || this.typeFilter || this.sourceFilter || this.tagFilter) this.runSearch()
  }

  async runSearch() {
    this.loading = true; this.error = ''
    const qp: Record<string, string> = {}
    if (this.q) qp['q'] = this.q; if (this.typeFilter) qp['type'] = this.typeFilter
    if (this.sourceFilter) qp['source'] = this.sourceFilter; if (this.tagFilter) qp['tag'] = this.tagFilter
    this.router.navigate([], { queryParams: qp, replaceUrl: true })
    try {
      const apiQp: Record<string, string> = {}
      if (this.q) apiQp['q'] = this.q; if (this.typeFilter) apiQp['type'] = this.typeFilter
      if (this.sourceFilter) apiQp['source_type'] = this.sourceFilter; if (this.tagFilter) apiQp['tag'] = this.tagFilter
      const params = new URLSearchParams(apiQp).toString()
      this.result = await firstValueFrom(this.http.get<SearchResult>(`${API_BASE}/knowledge/search${params ? '?' + params : ''}`))
    } catch (e) {
      this.error = errorMessage(e, 'Search failed')
    } finally { this.loading = false }
  }

  // lists are capped server-side; show "first N of M" when truncated
  countLabel(shown: number, total: number): string {
    return total > shown ? `first ${shown} of ${total}` : `${shown}`
  }

  copyShareLink() {
    const qp = new URLSearchParams()
    if (this.q) qp.set('q', this.q); if (this.typeFilter) qp.set('type', this.typeFilter)
    if (this.sourceFilter) qp.set('source', this.sourceFilter); if (this.tagFilter) qp.set('tag', this.tagFilter)
    navigator.clipboard.writeText(`${window.location.origin}/search?${qp}`)
    this.copied = true; setTimeout(() => this.copied = false, 2000)
  }
}
