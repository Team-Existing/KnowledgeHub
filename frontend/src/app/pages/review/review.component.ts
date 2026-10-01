import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { KnowledgeItem } from '../../models/api'

type ReviewItem = KnowledgeItem

@Component({
  selector: 'app-review',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './review.component.html'
})
export class ReviewComponent implements OnInit {
  items: ReviewItem[] = []; editingId: string | null = null
  editTitle = ''; editNote = ''; loading = false; error = ''

  constructor(private http: HttpClient) {}

  ngOnInit() { this.load() }

  async load() {
    try {
      this.items = await firstValueFrom(this.http.get<ReviewItem[]>(`${API_BASE}/knowledge/review`))
    } catch (e) {
      this.error = errorMessage(e, 'Could not load review queue')
      console.error('review load error:', e)
    }
  }

  startEdit(item: ReviewItem) { this.editingId = item.id; this.editTitle = item.title; this.editNote = '' }

  engineLabel(engine?: string) {
    return engine === 'cloud_llm' ? 'Cloud LLM' : engine === 'local_llm' ? 'Local LLM'
      : engine === 'structured' ? 'From source' : 'Regex'
  }

  engineClass(engine?: string) {
    return engine === 'cloud_llm' ? 'engine-cloud' : engine === 'local_llm' ? 'engine-local' : 'engine-regex'
  }

  async decide(id: string, status: 'accepted' | 'rejected', title?: string, note?: string) {
    this.loading = true
    try {
      await firstValueFrom(this.http.patch(`${API_BASE}/knowledge/review/${id}`, { status, note: note || '', title }))
      this.items = this.items.filter(i => i.id !== id); this.editingId = null
    } catch (e) { this.error = errorMessage(e, 'Could not update item') }
    finally { this.loading = false }
  }
}
