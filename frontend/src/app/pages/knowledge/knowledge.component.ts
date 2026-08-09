import { Component, OnInit, ElementRef, ViewChild, HostListener } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { AuthService, API_BASE } from '../../services/auth.service'

type KnowledgeItem = { id: string; title: string; type: string; author: string; date: string; tags: string[]; details: Record<string, unknown>; review_status: string }
type KnowledgeResponse = { artifacts: Array<{ id: string; title: string; author: string; created_at: string }>; knowledge_items: KnowledgeItem[]; relationships: Array<{ from: string; to: string; type: string }>; playbooks: any[] }
type GraphResponse = { nodes: Array<{ id: string; label: string; type: string }>; edges: Array<{ source: string; target: string; label: string }>; layout: string }
type IngestMode = 'text' | 'file' | 'url' | 'transcript'
type EdgeWithPos = { source: string; target: string; label: string; sx: number; sy: number; tx: number; ty: number }
type NodeWithPos = { id: string; label: string; type: string; x: number; y: number }
type Transform = { x: number; y: number; k: number }

@Component({
  selector: 'app-knowledge',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="knowledge-shell">
      <section class="toolbar">
        <div>
          <h2>Knowledge Hub</h2>
          <p>{{ data.artifacts.length }} artifacts · {{ data.knowledge_items.length }} items{{ pendingCount > 0 ? ' · ' + pendingCount + ' pending review' : '' }}</p>
        </div>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
          <a routerLink="/search"><button>Search</button></a>
          <button (click)="runCrossLink()" [disabled]="linkingCross">
            {{ linkingCross ? 'Linking…' : crossLinkCount !== null ? crossLinkCount + ' links found' : 'Cross-link' }}
          </button>
          <a *ngIf="pendingCount > 0" routerLink="/review"><button class="warning">Review ({{ pendingCount }})</button></a>
          <button class="primary" (click)="loadKnowledge()">Refresh</button>
        </div>
      </section>

      <div *ngIf="error" class="error-banner">{{ error }}</div>

      <section class="ingest-panel">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:0.75rem">
          <div>
            <h3>Ingest Artifact</h3>
            <p>Paste text, upload a file, fetch a URL, or extract decisions from a meeting transcript / email / Slack thread using AI.</p>
          </div>
          <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
            <button *ngFor="let m of ingestModes" [class.primary]="ingestMode === m.key" (click)="ingestMode = m.key">{{ m.label }}</button>
          </div>
        </div>

        <ng-container *ngIf="ingestMode === 'text'">
          <div class="form-grid">
            <input [(ngModel)]="title" placeholder="Title" />
            <input [(ngModel)]="author" placeholder="Author" />
            <input [(ngModel)]="tags" placeholder="Tags, comma separated" />
          </div>
          <textarea [(ngModel)]="content" placeholder="Paste knowledge source text here…" rows="8"></textarea>
          <div class="panel-actions">
            <span *ngIf="error" class="error-text">{{ error }}</span>
            <button class="primary" [disabled]="loading || !title || !content" (click)="ingestText()">
              {{ loading ? 'Extracting…' : 'Extract Knowledge' }}
            </button>
          </div>
        </ng-container>

        <ng-container *ngIf="ingestMode === 'file'">
          <div class="form-grid">
            <input [(ngModel)]="fileTitle" placeholder="Title" />
            <input [(ngModel)]="fileAuthor" placeholder="Author" />
            <input [(ngModel)]="fileTags" placeholder="Tags, comma separated" />
          </div>
          <input type="file" accept=".pdf,.txt,.md" style="padding:0.5rem 0" (change)="onFileChange($event)" />
          <div class="panel-actions">
            <span *ngIf="error" class="error-text">{{ error }}</span>
            <button class="primary" [disabled]="loading || !fileTitle" (click)="ingestFile()">
              {{ loading ? 'Uploading…' : 'Upload & Extract' }}
            </button>
          </div>
        </ng-container>

        <ng-container *ngIf="ingestMode === 'url'">
          <div class="form-grid">
            <input [(ngModel)]="urlTitle" placeholder="Title" />
            <input [(ngModel)]="urlAuthor" placeholder="Author" />
            <input [(ngModel)]="urlTags" placeholder="Tags, comma separated" />
          </div>
          <input [(ngModel)]="urlValue" placeholder="https://…" />
          <div class="panel-actions">
            <span *ngIf="error" class="error-text">{{ error }}</span>
            <button class="primary" [disabled]="loading || !urlValue || !urlTitle" (click)="ingestUrl()">
              {{ loading ? 'Fetching…' : 'Fetch & Extract' }}
            </button>
          </div>
        </ng-container>

        <ng-container *ngIf="ingestMode === 'transcript'">
          <div class="form-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
            <input [(ngModel)]="txTitle" placeholder="Title" />
            <input [(ngModel)]="txAuthor" placeholder="Author" />
            <input [(ngModel)]="txTags" placeholder="Tags, comma separated" />
            <select [(ngModel)]="txSourceType">
              <option value="transcript">Transcript</option>
              <option value="email">Email thread</option>
              <option value="slack">Slack thread</option>
            </select>
          </div>
          <textarea [(ngModel)]="txContent" placeholder="Paste your meeting transcript, email thread, or Slack conversation…" rows="10"></textarea>
          <div *ngIf="txSummary" class="summary-box"><strong>AI Summary:</strong> {{ txSummary }}</div>
          <p class="provider-hint">ⓘ Transcript mode uses your local model — no data leaves this device</p>
          <div class="panel-actions">
            <span *ngIf="error" class="error-text">{{ error }}</span>
            <button class="primary" [disabled]="loading || !txTitle || !txContent" (click)="ingestTranscript()">
              {{ loading ? 'Extracting with AI…' : 'Extract with AI' }}
            </button>
          </div>
        </ng-container>
      </section>

      <!-- Artifacts list with edit / delete -->
      <section class="ingest-panel" *ngIf="data.artifacts.length > 0">
        <h3>Artifacts <span style="font-weight:400;color:#667085;font-size:0.875rem">({{ data.artifacts.length }})</span></h3>
        <div class="artifact-list">
          <div *ngFor="let a of data.artifacts" class="artifact-row">
            <ng-container *ngIf="editingArtifactId !== a.id; else editArtifact">
              <div class="artifact-row-info">
                <strong>{{ a.title }}</strong>
                <span class="muted-text">by {{ a.author }} &middot; {{ a.created_at | date }}</span>
              </div>
              <div class="artifact-row-actions">
                <button (click)="startEditArtifact(a)">Edit</button>
                <button class="danger" (click)="deleteArtifact(a.id)" [disabled]="deletingId === a.id">
                  {{ deletingId === a.id ? 'Deleting…' : 'Delete' }}
                </button>
              </div>
            </ng-container>
            <ng-template #editArtifact>
              <div style="display:flex;flex-direction:column;gap:0.5rem;flex:1">
                <input [(ngModel)]="editArtifactTitle" placeholder="Title" />
                <input [(ngModel)]="editArtifactTags" placeholder="Tags, comma separated" />
              </div>
              <div class="artifact-row-actions">
                <button class="primary" (click)="saveArtifact(a.id)" [disabled]="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
                <button (click)="editingArtifactId = ''" [disabled]="saving">Cancel</button>
              </div>
            </ng-template>
          </div>
        </div>
      </section>

      <section class="graph-panel">
        <div class="graph-header">
          <div>
            <h3>Knowledge Graph</h3>
            <p>{{ graph.nodes.length }} nodes · {{ graph.edges.length }} relationships</p>
          </div>
          <div style="display:flex;align-items:center;gap:0.5rem">
            <a *ngIf="selectedNode && selectedNode.type !== 'artifact'" class="detail-link" [routerLink]="['/knowledge', selectedNode.id]">Open details</a>
          </div>
        </div>
        <ng-container *ngIf="graph.nodes.length > 0; else emptyGraph">
          <div class="graph-layout">
            <div class="graph-canvas-wrap">
              <div class="graph-zoom-bar">
                <button class="graph-zoom-btn" (click)="zoomIn()" title="Zoom in">+</button>
                <span class="graph-zoom-level">{{ (transform.k * 100) | number:'1.0-0' }}%</span>
                <button class="graph-zoom-btn" (click)="zoomOut()" title="Zoom out">−</button>
                <button class="graph-zoom-btn" (click)="resetView()" title="Reset view">⊙</button>
              </div>
              <svg #graphSvg class="knowledge-graph"
                role="img" aria-label="Knowledge graph visualization"
                (wheel)="onWheel($event)"
                (mousedown)="onSvgMouseDown($event)"
                (mousemove)="onMouseMove($event)"
                (mouseup)="onMouseUp($event)"
                (mouseleave)="onMouseUp($event)">
                <g [attr.transform]="svgTransform">
                  <g *ngFor="let edge of edgesWithPos">
                    <line [class]="isEdgeSelected(edge) ? 'graph-edge selected' : 'graph-edge'"
                      [attr.x1]="edge.sx" [attr.y1]="edge.sy" [attr.x2]="edge.tx" [attr.y2]="edge.ty" />
                    <text class="graph-edge-label" [attr.x]="(edge.sx+edge.tx)/2" [attr.y]="(edge.sy+edge.ty)/2-6">{{ edge.label }}</text>
                  </g>
                  <g *ngFor="let node of nodesWithPos"
                    [class]="selectedNodeId === node.id ? 'graph-node selected' : 'graph-node ' + node.type"
                    role="button" tabindex="0"
                    (click)="onNodeClick($event, node)"
                    (mousedown)="onNodeMouseDown($event, node)"
                    (keydown.enter)="selectedNodeId = node.id">
                    <circle [attr.cx]="node.x" [attr.cy]="node.y" [attr.r]="node.type === 'artifact' ? 24 : 18" />
                    <text [attr.x]="node.x" [attr.y]="node.y + (node.type === 'artifact' ? 34 : 28)">{{ node.label.length > 28 ? node.label.slice(0,25) + '…' : node.label }}</text>
                  </g>
                </g>
              </svg>
            </div>
            <aside class="graph-inspector">
              <ng-container *ngIf="selectedNode; else noSelection">
                <span class="type-pill">{{ selectedNode.type }}</span>
                <h4>{{ selectedNode.label }}</h4>
                <p>{{ selectedNodeLinks.length }} relationship{{ selectedNodeLinks.length === 1 ? '' : 's' }}</p>
                <p *ngIf="selectedNode.type === 'artifact'" class="muted-text">Artifact nodes are source documents; select a connected knowledge item to open its details.</p>
                <div class="relationship-list">
                  <button *ngFor="let edge of selectedNodeLinks" (click)="selectOther(edge)">
                    <span>{{ edge.label }}</span>
                    <strong>{{ getOtherLabel(edge) }}</strong>
                  </button>
                </div>
              </ng-container>
              <ng-template #noSelection>
                <div class="graph-empty">
                  <h4>Select a node</h4>
                  <p>Inspect its relationships and jump into the detail page.</p>
                </div>
              </ng-template>
            </aside>
          </div>
        </ng-container>
        <ng-template #emptyGraph>
          <div class="empty-state">
            <h3>No graph yet</h3>
            <p>Ingest an artifact to generate artifact-to-knowledge relationships.</p>
          </div>
        </ng-template>
      </section>

      <section class="filters">
        <div class="search-box">
          <input [(ngModel)]="query" placeholder="Filter extracted knowledge" />
        </div>
        <select [(ngModel)]="typeFilter">
          <option value="all">All types</option>
          <option *ngFor="let t of itemTypes" [value]="t">{{ t }}</option>
        </select>
        <a [routerLink]="['/search']" [queryParams]="query ? {q: query} : {}" style="font-size:0.85rem;align-self:center;color:#667eea;text-decoration:none">
          Advanced search →
        </a>
      </section>

      <section class="knowledge-grid">
        <a *ngFor="let item of filteredItems" class="knowledge-card knowledge-card-link" [routerLink]="['/knowledge', item.id]">
          <div class="card-topline">
            <span class="type-pill">{{ item.type }}</span>
            <span>{{ item.date | date }}</span>
          </div>
          <h3>{{ item.title }}</h3>
          <p>by {{ item.author }}</p>
          <span *ngIf="item.review_status === 'pending'" class="review-badge">pending review</span>
          <div class="tag-row"><span *ngFor="let t of item.tags">#{{ t }}</span></div>
        </a>
        <div *ngIf="filteredItems.length === 0" class="empty-state">
          <h3>No knowledge items yet</h3>
          <p>Add an artifact above to extract decisions, risks, best practices, and checklists.</p>
        </div>
      </section>
    </div>
  `
})
export class KnowledgeComponent implements OnInit {
  @ViewChild('graphSvg') graphSvgRef!: ElementRef<SVGSVGElement>

  data: KnowledgeResponse = { artifacts: [], knowledge_items: [], relationships: [], playbooks: [] }
  graph: GraphResponse = { nodes: [], edges: [], layout: 'force-directed' }
  query = ''; typeFilter = 'all'; loading = false; error = ''
  selectedNodeId = ''; ingestMode: IngestMode = 'text'
  title = ''; author = ''; tags = ''; content = ''
  fileTitle = ''; fileAuthor = ''; fileTags = ''; selectedFile: File | null = null
  urlValue = ''; urlTitle = ''; urlAuthor = ''; urlTags = ''
  txTitle = ''; txAuthor = ''; txTags = ''; txContent = ''; txSourceType = 'transcript'; txSummary = ''
  linkingCross = false; crossLinkCount: number | null = null

  // artifact CRUD
  editingArtifactId = ''; editArtifactTitle = ''; editArtifactTags = ''
  deletingId = ''; saving = false

  // ── pan / zoom state ────────────────────────────────────────────────────
  transform: Transform = { x: 0, y: 0, k: 1 }
  private _panning = false
  private _panStart = { x: 0, y: 0 }
  private _draggingNode: NodeWithPos | null = null
  private _dragMoved = false
  private readonly ZOOM_MIN = 0.2
  private readonly ZOOM_MAX = 4
  private readonly ZOOM_STEP = 0.15

  get svgTransform() {
    return `translate(${this.transform.x},${this.transform.y}) scale(${this.transform.k})`
  }

  readonly ingestModes = [
    { key: 'text' as IngestMode, label: 'Text' },
    { key: 'file' as IngestMode, label: 'File' },
    { key: 'url' as IngestMode, label: 'URL' },
    { key: 'transcript' as IngestMode, label: 'Transcript / Email / Slack' },
  ]

  private posMap = new Map<string, { x: number; y: number }>()

  constructor(private http: HttpClient, public auth: AuthService) {}

  ngOnInit() { this.loadKnowledge() }

  get filteredItems() {
    return this.data.knowledge_items.filter(item => {
      const matchQ = `${item.title} ${item.tags.join(' ')}`.toLowerCase().includes(this.query.toLowerCase())
      return matchQ && (this.typeFilter === 'all' || item.type === this.typeFilter)
    })
  }

  get itemTypes() { return [...new Set(this.data.knowledge_items.map(i => i.type))].sort() }
  get pendingCount() { return this.data.knowledge_items.filter(i => i.review_status === 'pending').length }
  get selectedNode() { return this.graph.nodes.find(n => n.id === this.selectedNodeId) }
  get selectedNodeLinks() {
    if (!this.selectedNode) return []
    return this.graph.edges.filter(e => e.source === this.selectedNodeId || e.target === this.selectedNodeId)
  }

  get nodesWithPos(): NodeWithPos[] {
    return this.graph.nodes.map(n => ({ ...n, ...this.getPos(n.id) }))
  }

  get edgesWithPos(): EdgeWithPos[] {
    return this.graph.edges
      .map(e => {
        const s = this.getPos(e.source), t = this.getPos(e.target)
        return { ...e, sx: s.x, sy: s.y, tx: t.x, ty: t.y }
      })
  }

  private getPos(id: string): { x: number; y: number } {
    if (!this.posMap.has(id)) this.buildPositions()
    return this.posMap.get(id) || { x: 0, y: 0 }
  }

  private buildPositions() {
    this.posMap.clear()
    const cx = 380, cy = 210
    const artifacts = this.graph.nodes.filter(n => n.type === 'artifact')
    const knowledge = this.graph.nodes.filter(n => n.type !== 'artifact')
    artifacts.forEach((n, i) => {
      this.posMap.set(n.id, { x: cx, y: Math.max(72, cy + (i - (artifacts.length - 1) / 2) * 88) })
    })
    knowledge.forEach((n, i) => {
      const angle = (i / Math.max(knowledge.length, 1)) * Math.PI * 2 - Math.PI / 2
      this.posMap.set(n.id, { x: cx + Math.cos(angle) * 300, y: cy + Math.sin(angle) * 165 })
    })
  }

  isEdgeSelected(edge: any) { return this.selectedNodeId === edge.source || this.selectedNodeId === edge.target }

  selectOther(edge: any) {
    this.selectedNodeId = edge.source === this.selectedNodeId ? edge.target : edge.source
  }

  getOtherLabel(edge: any): string {
    const otherId = edge.source === this.selectedNodeId ? edge.target : edge.source
    return this.graph.nodes.find(n => n.id === otherId)?.label || otherId
  }

  // ── zoom / pan handlers ─────────────────────────────────────────────────

  onWheel(e: WheelEvent) {
    e.preventDefault()
    const svg = this.graphSvgRef?.nativeElement
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const delta = e.deltaY < 0 ? this.ZOOM_STEP : -this.ZOOM_STEP
    this._applyZoom(delta, mx, my)
  }

  zoomIn()  { const c = this._center(); this._applyZoom( this.ZOOM_STEP, c.x, c.y) }
  zoomOut() { const c = this._center(); this._applyZoom(-this.ZOOM_STEP, c.x, c.y) }

  resetView() {
    this.transform = { x: 0, y: 0, k: 1 }
  }

  private _applyZoom(delta: number, mx: number, my: number) {
    const k0 = this.transform.k
    const k1 = Math.min(this.ZOOM_MAX, Math.max(this.ZOOM_MIN, k0 + delta))
    const ratio = k1 / k0
    this.transform = {
      x: mx - ratio * (mx - this.transform.x),
      y: my - ratio * (my - this.transform.y),
      k: k1,
    }
  }

  private _center(): { x: number; y: number } {
    const svg = this.graphSvgRef?.nativeElement
    if (!svg) return { x: 380, y: 210 }
    const r = svg.getBoundingClientRect()
    return { x: r.width / 2, y: r.height / 2 }
  }

  onSvgMouseDown(e: MouseEvent) {
    if (this._draggingNode) return
    this._panning = true
    this._panStart = { x: e.clientX - this.transform.x, y: e.clientY - this.transform.y }
  }

  onNodeMouseDown(e: MouseEvent, node: NodeWithPos) {
    e.stopPropagation()
    this._draggingNode = node
    this._dragMoved = false
  }

  onNodeClick(e: MouseEvent, node: NodeWithPos) {
    if (!this._dragMoved) this.selectedNodeId = node.id
  }

  onMouseMove(e: MouseEvent) {
    if (this._draggingNode) {
      this._dragMoved = true
      const k = this.transform.k
      const svg = this.graphSvgRef?.nativeElement
      if (!svg) return
      const rect = svg.getBoundingClientRect()
      const svgX = (e.clientX - rect.left - this.transform.x) / k
      const svgY = (e.clientY - rect.top  - this.transform.y) / k
      this.posMap.set(this._draggingNode.id, { x: svgX, y: svgY })
      return
    }
    if (!this._panning) return
    this.transform = {
      ...this.transform,
      x: e.clientX - this._panStart.x,
      y: e.clientY - this._panStart.y,
    }
  }

  onMouseUp(_e: MouseEvent) {
    this._panning = false
    this._draggingNode = null
  }

  @HostListener('window:mouseup')
  onWindowMouseUp() { this._panning = false; this._draggingNode = null }

  async loadKnowledge() {
    try {
      const headers = this.auth.authHeaders()
      const [kr, gr]: any[] = await Promise.all([
        firstValueFrom(this.http.get(`${API_BASE}/knowledge`, { headers })),
        firstValueFrom(this.http.get(`${API_BASE}/knowledge/graph`, { headers })),
      ])
      this.data = kr
      this.graph = gr
      this.posMap.clear()
    } catch (e: any) {
      this.error = e?.error?.detail || e?.message || 'Failed to load knowledge'
      console.error('loadKnowledge error:', e)
    }
  }

  async ingestText() {
    this.loading = true; this.error = ''
    try {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/artifacts`, {
        title: this.title, author: this.author || 'unknown',
        tags: this.tags.split(',').map(t => t.trim()).filter(Boolean),
        content: this.content, source: 'manual'
      }, { headers: this.auth.authHeaders() }))
      this.title = ''; this.author = ''; this.tags = ''; this.content = ''
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'Ingestion failed' }
    finally { this.loading = false }
  }

  onFileChange(e: Event) {
    this.selectedFile = (e.target as HTMLInputElement).files?.[0] || null
  }

  async ingestFile() {
    if (!this.selectedFile || !this.fileTitle) return
    this.loading = true; this.error = ''
    try {
      const form = new FormData()
      form.append('file', this.selectedFile)
      form.append('title', this.fileTitle)
      form.append('author', this.fileAuthor || 'unknown')
      form.append('tags', this.fileTags)
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/artifacts/upload`, form, { headers: this.auth.authHeaders() }))
      this.fileTitle = ''; this.fileAuthor = ''; this.fileTags = ''; this.selectedFile = null
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'Upload failed' }
    finally { this.loading = false }
  }

  async ingestUrl() {
    this.loading = true; this.error = ''
    try {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/artifacts/url`, {
        url: this.urlValue, title: this.urlTitle,
        author: this.urlAuthor || 'unknown',
        tags: this.urlTags.split(',').map(t => t.trim()).filter(Boolean)
      }, { headers: this.auth.authHeaders() }))
      this.urlValue = ''; this.urlTitle = ''; this.urlAuthor = ''; this.urlTags = ''
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'URL fetch failed' }
    finally { this.loading = false }
  }

  async ingestTranscript() {
    this.loading = true; this.error = ''; this.txSummary = ''
    try {
      const result: any = await firstValueFrom(this.http.post(`${API_BASE}/knowledge/artifacts/transcript`, {
        title: this.txTitle, content: this.txContent,
        source_type: this.txSourceType,
        author: this.txAuthor || 'unknown',
        tags: this.txTags.split(',').map(t => t.trim()).filter(Boolean)
      }, { headers: this.auth.authHeaders() }))
      if (result.summary) this.txSummary = result.summary
      this.txTitle = ''; this.txAuthor = ''; this.txTags = ''; this.txContent = ''
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'Transcript ingestion failed' }
    finally { this.loading = false }
  }

  startEditArtifact(a: any) {
    this.editingArtifactId = a.id
    this.editArtifactTitle = a.title
    this.editArtifactTags = (a.tags || []).join(', ')
  }

  async saveArtifact(id: string) {
    this.saving = true; this.error = ''
    try {
      await firstValueFrom(this.http.put(`${API_BASE}/knowledge/artifacts/${id}`, {
        title: this.editArtifactTitle,
        tags: this.editArtifactTags.split(',').map((t: string) => t.trim()).filter(Boolean),
      }, { headers: this.auth.authHeaders() }))
      this.editingArtifactId = ''
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'Save failed' }
    finally { this.saving = false }
  }

  async deleteArtifact(id: string) {
    if (!confirm('Delete this artifact and all its extracted knowledge items?')) return
    this.deletingId = id; this.error = ''
    try {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/artifacts/${id}`, { headers: this.auth.authHeaders() }))
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'Delete failed' }
    finally { this.deletingId = '' }
  }

  async runCrossLink() {
    this.linkingCross = true; this.crossLinkCount = null
    try {
      const result: any = await firstValueFrom(this.http.post(`${API_BASE}/knowledge/link`, {}, { headers: this.auth.authHeaders() }))
      this.crossLinkCount = result.links_created
      await this.loadKnowledge()
    } catch (e: any) { this.error = e?.message || 'Cross-link failed' }
    finally { this.linkingCross = false }
  }
}
