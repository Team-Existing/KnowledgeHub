import { Component, ViewChild, ElementRef, AfterViewChecked } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { DomSanitizer, SafeHtml } from '@angular/platform-browser'
import { Router } from '@angular/router'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { Citation, ContextNode, GraphRagResponse } from '../../models/api'
import { ModelService } from '../../services/model.service'
import { IconComponent } from '../../components/icon/icon.component'

//Update Message type - citations can be string[] or Citation[]
type Message = { 
  role: 'user' | 'assistant'; 
  content: string; 
  citations?: string[] | Citation[];  // ← Support both formats
  context_nodes?: ContextNode[]; 
  retrieval_mode?: string; 
  ts: number 
}

const PIPELINE_STAGES = [
  'Rewriting your question…',
  'Generating hypothetical answer…',
  'Searching knowledge graph…',
  'Retrieving context nodes…',
  'Ranking results…',
  'Filtering by relevance…',
  'Generating answer…',
  'Finalising response…',
]

@Component({
  selector: 'app-graphrag',
  standalone: true,
  imports: [CommonModule, FormsModule, IconComponent],
  templateUrl: './graphrag.component.html',
  styleUrl: './graphrag.component.css'
})

export class GraphragComponent implements AfterViewChecked {
  @ViewChild('bottomEl') bottomEl!: ElementRef
  messages: Message[] = this._loadMessages()
  input = ''
  loading = false
  topK = 8
  expandedCtx: number | null = null
  pipelineStage = ''
  private shouldScroll = false
  private _stageInterval: ReturnType<typeof setInterval> | null = null

  constructor(
    private http: HttpClient, 
    public modelService: ModelService, 
    private sanitizer: DomSanitizer,
    private router: Router
  ) {}

  isNoAnswer(content: string): boolean {
    const noAnswerPhrases = [
      'no relevant knowledge found',
      'does not contain sufficiently relevant information',
      'I don\'t have an answer',
      'I do not have a clear definition',
      'does not provide any further details',
      'No relevant knowledge found'
    ];
    return noAnswerPhrases.some(phrase => content.toLowerCase().includes(phrase.toLowerCase()));
  }

  //Helper to normalize citations to objects
  private _normalizeCitations(citations: string[] | Citation[] | undefined): Citation[] {
    if (!citations || citations.length === 0) {
      return []
    }
    // If first element is a string, convert to Citation objects
    if (typeof citations[0] === 'string') {
      return (citations as string[]).map(id => ({
        id,
        title: id,
        type: 'unknown'
      }))
    }
    return citations as Citation[]
  }

  getCitations(citations: string[] | Citation[] | undefined): Citation[] {
    return this._normalizeCitations(citations)
  }

  private _loadMessages(): Message[] {
    try { 
      const raw = JSON.parse(sessionStorage.getItem('graphrag_messages') || '[]')
      // Ensure backward compatibility
      return (raw as Message[]).map(m => ({
        ...m,
        citations: m.citations || []
      }))
    } catch { 
      return [] 
    }
  }

  private _saveMessages() {
    sessionStorage.setItem('graphrag_messages', JSON.stringify(this.messages))
  }

  confirmClear() {
    if (this.messages.length === 0 || confirm('Clear conversation history?')) {
      this.messages = []
      sessionStorage.removeItem('graphrag_messages')
    }
  }

  goToItem(id: string) {
    this.router.navigate(['/knowledge', id])
  }
    
  //Updated renderMarkdown to handle citations
  renderMarkdown(text: string, citations?: string[] | Citation[]): SafeHtml {
    const esc = (s: string) => this._escapeHtml(s)
    let html = esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/^- (.+)$/gm, '<li>$1</li>')
      .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
      .replace(/\n/g, '<br>')
    
    // Replace citation IDs with human-readable titles if available
    if (citations && citations.length > 0) {
      const normalized = this._normalizeCitations(citations)
      if (normalized.length > 0) {
        // Citations are always the full id in brackets: [item_id] or
        // [id1, id2]. `html` is already escaped, so key the map by escaped id;
        // titles and ids come from stored data and are escaped on output.
        const citeMap = new Map(normalized.map(c => [esc(c.id), c]))
        html = html.replace(/\[([^\]]+)\]/g, (match, inner: string) => {
          const ids = inner.split(',').map(s => s.trim())
          if (!ids.every(id => citeMap.has(id))) return match
          return ids.map(id => {
            const c = citeMap.get(id)!
            return `<span class="citation-badge" title="Source: ${esc(c.id)}">${esc(c.title)}</span>`
          }).join(' ')
        })
      }
    }
    
    return this.sanitizer.bypassSecurityTrustHtml(html)
  }

  private _escapeHtml(s: string): string {
    return String(s ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
  }

  ngAfterViewChecked() {
    if (this.shouldScroll) { 
      this.bottomEl?.nativeElement?.scrollIntoView({ behavior: 'smooth' })
      this.shouldScroll = false 
    }
  }

  toggleCtx(idx: number) { 
    this.expandedCtx = this.expandedCtx === idx ? null : idx 
  }

  onKey(e: KeyboardEvent) { 
    if (e.key === 'Enter' && !e.shiftKey) { 
      e.preventDefault()
      this.send() 
    } 
  }

  fmt(ts: number) { 
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) 
  }

  private _startPipelineProgress() {
    let i = 0
    this.pipelineStage = PIPELINE_STAGES[0]
    this._stageInterval = setInterval(() => {
      i = Math.min(i + 1, PIPELINE_STAGES.length - 1)
      this.pipelineStage = PIPELINE_STAGES[i]
    }, 1200)
  }

  private _stopPipelineProgress() {
    if (this._stageInterval) { 
      clearInterval(this._stageInterval)
      this._stageInterval = null 
    }
    this.pipelineStage = ''
  }

  async send() {
    const q = this.input.trim()
    if (!q || this.loading) return
    this.input = ''
    
    const history = this.messages.slice(-6).map(m => ({ role: m.role, content: m.content }))
    this.messages.push({ role: 'user', content: q, ts: Date.now() })
    this.loading = true
    this.shouldScroll = true
    this._startPipelineProgress()
    
    try {
      const data = await firstValueFrom(
        this.http.post<GraphRagResponse>(
          `${API_BASE}/knowledge/graphrag/query`,
          { question: q, top_k: this.topK, history }
        )
      )

      const contextNodes = data.context_nodes || []
      const nodeMap = new Map<string, ContextNode>()
      contextNodes.forEach(node => {
        if (node.id) nodeMap.set(node.id, node)
      })

      let citations: Citation[] = []

      if (data.citations && data.citations.length > 0) {
        // older servers returned bare id strings
        citations = (data.citations as Array<Citation | string>).map(cite => {
          if (typeof cite === 'string') {
            const node = nodeMap.get(cite)
            if (node) {
              return {
              id: node.id,  // ✅ Use the full ID from the node
              title: node.title || node.label || cite,
              type: node.kind || node.type || 'item',
              status: node.status ?? undefined,
            }
          }
          return { id: cite, title: cite, type: 'unknown' }
        }
        return cite
      })
    }
         
      const hasRealAnswer = !data.answer?.includes('No relevant knowledge found') &&
                          !data.answer?.includes('I don\'t have an answer') &&
                          !data.answer?.includes('does not contain sufficiently relevant information')
      
      this.messages.push({
        role: 'assistant',
        content: data.answer,
        citations: hasRealAnswer ? citations : [],
        context_nodes: contextNodes,
        retrieval_mode: data.retrieval_mode,
        ts: Date.now()
      })
    } catch (e) {
      this.messages.push({
        role: 'assistant',
        content: `Error: ${errorMessage(e, 'Request failed')}`,
        ts: Date.now()
      })
    } finally {
      this._stopPipelineProgress()
      this.loading = false
      this.shouldScroll = true
      this._saveMessages()
    }
  }
}
