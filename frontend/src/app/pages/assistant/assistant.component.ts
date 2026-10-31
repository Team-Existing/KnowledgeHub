import { Component } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { AssistantRun } from '../../models/api'

const EXAMPLES = [
  'What should I understand before changing Payments?',
  'Should we move billing off PostgreSQL?',
  'Which approaches have we tried and abandoned?',
  'What needs attention: anything out of date or overdue?',
]

/**
 * One question, answered by the agents that fit it (backend services/orchestrator.py):
 * the plan with the reason for each step, each step's result, and which sentences
 * of the answer no source backs.
 */
@Component({
  selector: 'app-assistant',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './assistant.component.html'
})
export class AssistantComponent {
  question = ''
  run: AssistantRun | null = null
  error = ''; loading = false
  readonly examples = EXAMPLES

  constructor(private http: HttpClient) {}

  async ask(question = this.question) {
    this.question = question.trim()
    if (this.question.length < 3) return
    this.loading = true; this.error = ''; this.run = null
    try {
      this.run = await firstValueFrom(this.http.post<AssistantRun>(`${API_BASE}/knowledge/assistant`, { question: this.question }))
    } catch (e) { this.error = errorMessage(e, 'The assistant could not answer') }
    finally { this.loading = false }
  }

  plural(n: number, word: string): string { return `${n} ${word}${n === 1 ? '' : 's'}` }
}
