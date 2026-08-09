import { Component, ViewChild, ElementRef, AfterViewChecked } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { DomSanitizer, SafeHtml } from '@angular/platform-browser'
import { AuthService, API_BASE } from '../../services/auth.service'
import { ModelService } from '../../services/model.service'

//Define Citation type
type Citation = { id: string; title: string; type: string }
type ContextNode = { id: string; title?: string; label?: string; kind?: string; type?: string; score?: number; retrieved_by?: string }

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
  imports: [CommonModule, FormsModule],
  template: `
    <div class="chat-container">
      <div class="chat-header">
        <div>
          <strong style="display:flex;align-items:center;gap:0.5rem">GraphRAG Assistant</strong>
          <p style="color:#667085;font-size:0.8rem;margin-top:0.2rem">Graph-aware retrieval · answers grounded in your knowledge base</p>
        </div>
        <div class="chat-actions">
          <span class="provider-badge" [ngClass]="'provider-' + modelService.statusBadgeColor()" [attr.aria-label]="modelService.statusBadgeText()">
            {{ modelService.statusBadgeText() }}
          </span>
          <label style="font-size:0.8rem;color:#667085;display:flex;align-items:center;gap:0.4rem">
            Top-K
            <input type="number" min="1" max="20" [(ngModel)]="topK" style="width:52px;padding:0.25rem 0.4rem;font-size:0.8rem" />
          </label>
          <button (click)="confirmClear()" title="Clear chat">🗑</button>
        </div>
      </div>

      <div class="messages" #messagesEl>
        @if (messages.length === 0) {
          <div style="text-align:center;color:#667085;margin-top:3rem">
            <p>Ask a question about your knowledge base.</p>
          </div>
        }
        @for (m of messages; track m.ts; let idx = $index) {
          <div [class]="'message ' + m.role">
            <div class="message-header">
              <strong>{{ m.role === 'user' ? 'You' : 'Assistant' }}</strong>
              <span class="timestamp">{{ fmt(m.ts) }}</span>
            </div>
            <!-- Use renderMarkdown with citations -->
            <div style="line-height:1.6" [innerHTML]="renderMarkdown(m.content, m.citations)"></div>
            
            <!-- Citations section - use getCitations() helper -->
            @if (m.role === 'assistant' && m.citations && m.citations.length > 0 && !isNoAnswer(m.content)) {
              <div class="citations-section">
                <details>
                  <summary>Sources ({{ m.citations.length }})</summary>
                  <ul class="citation-list">
                    @for (cite of getCitations(m.citations); track cite.id) {
                      <li>
                        <span class="citation-type">{{ cite.type }}</span>
                        <span class="citation-title">{{ cite.title }}</span>
                        <button (click)="goToItem(cite.id)" class="citation-link">View →</button>
                      </li>
                    }
                  </ul>
                </details>
              </div>
            }
          </div>
        }
        @if (loading) {
          <div class="typing-indicator" aria-live="polite" aria-atomic="true">
            {{ pipelineStage }} <span class="latency-hint">Generating locally — no data leaves this device</span>
          </div>
        }
        <div #bottomEl></div>
      </div>

      <div class="input-area">
        <textarea rows="2" placeholder="Ask about decisions, risks, lessons, best practices…"
          [(ngModel)]="input" (keydown)="onKey($event)" [disabled]="loading"></textarea>
        <button class="primary" (click)="send()" [disabled]="loading || !input.trim()" title="Send">➤</button>
      </div>
    </div>
  `,
  styles: [`
    .provider-badge {
      padding: 0.25rem 0.6rem;
      border-radius: 4px;
      font-size: 0.78rem;
      font-weight: 500;
    }
    .provider-green { background: #e6f7e6; color: #1a6b1a; }
    .provider-blue  { background: #ede9ff; color: #5a3fc0; }
    .provider-red   { background: #ffe6e6; color: #cc0000; }
    .latency-hint   { color: #667085; font-size: 0.75rem; margin-left: 0.5rem; }

    .citations-section {
      margin-top: 0.75rem;
      padding: 0.5rem 0.75rem;
      background: #f8f9fa;
      border-radius: 6px;
      border-left: 3px solid #667eea;
    }

    .citations-section summary {
      cursor: pointer;
      font-size: 0.85rem;
      color: #344054;
      font-weight: 500;
    }

    .citation-list {
      list-style: none;
      padding: 0;
      margin: 0.5rem 0 0;
    }

    .citation-list li {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.3rem 0;
      font-size: 0.85rem;
      border-bottom: 1px solid #f0f0f0;
    }

    .citation-list li:last-child {
      border-bottom: none;
    }

    .citation-type {
      background: #e5ecea;
      padding: 0.1rem 0.5rem;
      border-radius: 3px;
      font-size: 0.7rem;
      color: #475467;
      text-transform: uppercase;
    }

    .citation-title {
      flex: 1;
      font-weight: 500;
      color: #1f2933;
    }

    .citation-link {
      background: #667eea;
      color: white;
      border: none;
      padding: 0.15rem 0.5rem;
      border-radius: 3px;
      font-size: 0.7rem;
      cursor: pointer;
    }

    .citation-link:hover {
      background: #5a6fd6;
    }

    .citation-badge {
      display: inline-block;
      background: #ede9ff;
      color: #5a3fc0;
      padding: 0.1rem 0.5rem;
      border-radius: 4px;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid #d4c9ff;
    }

    .citation-badge:hover {
      background: #ddd4ff;
    }
  `]
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
  private _stageInterval: any = null

  constructor(
    private http: HttpClient, 
    public auth: AuthService, 
    public modelService: ModelService, 
    private sanitizer: DomSanitizer
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
      return raw.map((m: any) => ({
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
    console.log('Opening item:', id);
    let cleanId = id;
    if (!id.includes('_')) {
    // Try to find the full ID from context nodes
    for (const msg of this.messages) {
      if (msg.context_nodes) {
        for (const node of msg.context_nodes) {
          const nodeId = node.id || '';
          if (nodeId.endsWith(id)) {
            cleanId = nodeId;
            break;
          }
        }
      }
      if (cleanId !== id) break;
    }
  }
    const url = `/knowledge/${cleanId}`;
    console.log('📍 Navigating to:', url);
    
    // Use router instead of window.open for better SPA navigation
    // But since we want a new tab, use window.open
    window.open(url, '_blank');
  }
    
  //Updated renderMarkdown to handle citations
  renderMarkdown(text: string, citations?: string[] | Citation[]): SafeHtml {
    let html = text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/^- (.+)$/gm, '<li>$1</li>')
      .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
      .replace(/\n/g, '<br>')
    
    // Replace citation IDs with human-readable titles if available
    if (citations && citations.length > 0) {
      const normalized = this._normalizeCitations(citations)
      if (normalized.length > 0) {
        const citeMap = new Map(normalized.map(c => [c.id, c.title]))
        html = html.replace(/\[([^\]]+)\]/g, (match, id) => {
          // Try exact match
          if (citeMap.has(id)) {
            return `<span class="citation-badge" title="Source: ${id}">${citeMap.get(id)}</span>`
          }
          // Try with short ID (last part after underscore)
          for (const [fullId, fullTitle] of citeMap) {
            const shortId = fullId.split('_').pop() || ''
            if (id === shortId || id === shortId.slice(0, 6)) {
              return `<span class="citation-badge" title="Source: ${fullId}">${fullTitle}</span>`
            }
          }
          return match
        })
      }
    }
    
    return this.sanitizer.bypassSecurityTrustHtml(html)
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
      const data: any = await firstValueFrom(
        this.http.post(
          `${API_BASE}/knowledge/graphrag/query`,
          { question: q, top_k: this.topK, history },
          { headers: this.auth.authHeaders() }
        )
      )

      const contextNodes = data.context_nodes || []
      const nodeMap = new Map<string, any>()
      contextNodes.forEach((node: any) => {
        if (node.id) {
          nodeMap.set(node.id, node)
          // Also store by short ID (last part after _)
          const shortId = node.id.split('_').pop()
          if (shortId) {
            nodeMap.set(shortId, node)
          }
        }
      })
      
      let citations: Citation[] = []
      
      if (data.citations && data.citations.length > 0) {
        citations = data.citations.map((cite: any) => {
          if (typeof cite === 'string') {
            // Try to find the node
            let node = nodeMap.get(cite)
            if (!node) {
              // Try short ID match
              const shortId = cite.split('_').pop()
              if (shortId) {
                node = nodeMap.get(shortId)
              }
            }
            if (node) {
              return {
              id: node.id,  // ✅ Use the full ID from the node
              title: node.title || node.label || cite,
              type: node.kind || node.type || 'item'
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
    } catch (e: any) {
      this.messages.push({
        role: 'assistant',
        content: `Error: ${e?.message || 'Request failed'}`,
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
