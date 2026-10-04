import { Component, OnInit } from '@angular/core'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { KnowledgeItem, KnowledgeResponse, Playbook, PlaybookStep } from '../../models/api'

/** Item types that make sense as playbook steps */
const STEP_TYPES = new Set(['how-to', 'checklist', 'best-practice', 'action-item', 'lesson'])

@Component({
  selector: 'app-playbooks',
  imports: [FormsModule, RouterLink],
  templateUrl: './playbooks.component.html'
})
export class PlaybooksComponent implements OnInit {
  playbooks: Playbook[] = []
  stepItems: KnowledgeItem[] = []
  error = ''; busy = false

  // editor
  editingId: string | null = null
  editingTitle = ''          // title when editing started (a rename replaces the old playbook)
  title = ''
  steps: PlaybookStep[] = []
  newStep = ''
  pickItemId = ''

  constructor(private http: HttpClient) {}

  async ngOnInit() { await this.load() }

  async load() {
    try {
      const [playbooks, knowledge] = await Promise.all([
        firstValueFrom(this.http.get<Playbook[]>(`${API_BASE}/knowledge/playbooks`)),
        firstValueFrom(this.http.get<KnowledgeResponse>(`${API_BASE}/knowledge`)),
      ])
      this.playbooks = playbooks
      this.stepItems = knowledge.knowledge_items.filter(i => STEP_TYPES.has(i.type) && i.review_status !== 'rejected')
    } catch (e) { this.error = errorMessage(e, 'Could not load playbooks') }
  }

  itemTitle(id?: string): string | null {
    return id ? this.stepItems.find(i => i.id === id)?.title ?? null : null
  }

  startNew() { this.editingId = null; this.editingTitle = ''; this.title = ''; this.steps = []; this.error = '' }

  edit(p: Playbook) {
    this.editingId = p.id; this.editingTitle = p.title
    this.title = p.title; this.steps = p.steps.map(s => ({ ...s })); this.error = ''
  }

  addTextStep() {
    const text = this.newStep.trim()
    if (text) this.steps.push({ text })
    this.newStep = ''
  }

  addItemStep() {
    const item = this.stepItems.find(i => i.id === this.pickItemId)
    if (item) this.steps.push({ text: item.title, item_id: item.id })
    this.pickItemId = ''
  }

  move(index: number, delta: number) {
    const to = index + delta
    if (to < 0 || to >= this.steps.length) return
    const [step] = this.steps.splice(index, 1)
    this.steps.splice(to, 0, step)
  }

  removeStep(index: number) { this.steps.splice(index, 1) }

  async save() {
    this.busy = true; this.error = ''
    try {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/playbooks`, { title: this.title, steps: this.steps }))
      // the id follows the title, so a rename saved a new playbook: remove the old one
      if (this.editingId && this.title.trim().toLowerCase() !== this.editingTitle.trim().toLowerCase()) {
        await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/playbooks/${this.editingId}`))
      }
      this.startNew()
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not save the playbook') }
    finally { this.busy = false }
  }

  async remove(p: Playbook) {
    if (!confirm(`Delete the playbook “${p.title}”?`)) return
    this.busy = true; this.error = ''
    try {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/playbooks/${p.id}`))
      if (this.editingId === p.id) this.startNew()
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not delete the playbook') }
    finally { this.busy = false }
  }
}
