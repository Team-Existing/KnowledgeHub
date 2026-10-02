import { Component, OnInit, ElementRef, ViewChild, HostListener } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { RouterLink } from '@angular/router'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { Artifact, CrossLinkResult, GraphEdge, GraphResponse, IngestResult, KnowledgeResponse } from '../../models/api'

type IngestMode = 'text' | 'file' | 'url' | 'transcript'
type EdgeWithPos = { source: string; target: string; label: string; sx: number; sy: number; tx: number; ty: number }
type NodeWithPos = { id: string; label: string; type: string; x: number; y: number }
type Transform = { x: number; y: number; k: number }

@Component({
  selector: 'app-knowledge',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './knowledge.component.html'
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

  // OKF (Open Knowledge Format) import / export of the active space
  okfBusy = false
  okfMessage = ''

  async exportOkf() {
    this.okfBusy = true; this.okfMessage = ''; this.error = ''
    try {
      const payload = await firstValueFrom(this.http.get<unknown>(`${API_BASE}/knowledge/okf/export`))
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `knowledge-hubs-export-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      this.okfMessage = 'Export downloaded.'
    } catch (e) { this.error = errorMessage(e, 'Export failed') }
    finally { this.okfBusy = false }
  }

  async importOkf(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''   // allow picking the same file again
    if (!file) return
    this.okfBusy = true; this.okfMessage = ''; this.error = ''
    try {
      let payload: unknown
      try { payload = JSON.parse(await file.text()) } catch { throw new Error('That file is not valid JSON') }
      const r = await firstValueFrom(this.http.post<{ imported_items: number; relationships: number }>(
        `${API_BASE}/knowledge/okf/import`, payload))
      this.okfMessage = `Imported ${r.imported_items} item${r.imported_items === 1 ? '' : 's'} and ${r.relationships} relationship${r.relationships === 1 ? '' : 's'} from ${file.name}.`
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Import failed') }
    finally { this.okfBusy = false }
  }

  constructor(private http: HttpClient) {}

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

  isEdgeSelected(edge: GraphEdge) { return this.selectedNodeId === edge.source || this.selectedNodeId === edge.target }

  selectOther(edge: GraphEdge) {
    this.selectedNodeId = edge.source === this.selectedNodeId ? edge.target : edge.source
  }

  getOtherLabel(edge: GraphEdge): string {
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
      const [kr, gr] = await Promise.all([
        firstValueFrom(this.http.get<KnowledgeResponse>(`${API_BASE}/knowledge`)),
        firstValueFrom(this.http.get<GraphResponse>(`${API_BASE}/knowledge/graph`)),
      ])
      this.data = kr
      this.graph = gr
      this.posMap.clear()
    } catch (e) {
      this.error = errorMessage(e, 'Failed to load knowledge')
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
      }))
      this.title = ''; this.author = ''; this.tags = ''; this.content = ''
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Ingestion failed') }
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
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/artifacts/upload`, form))
      this.fileTitle = ''; this.fileAuthor = ''; this.fileTags = ''; this.selectedFile = null
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Upload failed') }
    finally { this.loading = false }
  }

  async ingestUrl() {
    this.loading = true; this.error = ''
    try {
      await firstValueFrom(this.http.post(`${API_BASE}/knowledge/artifacts/url`, {
        url: this.urlValue, title: this.urlTitle,
        author: this.urlAuthor || 'unknown',
        tags: this.urlTags.split(',').map(t => t.trim()).filter(Boolean)
      }))
      this.urlValue = ''; this.urlTitle = ''; this.urlAuthor = ''; this.urlTags = ''
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'URL fetch failed') }
    finally { this.loading = false }
  }

  async ingestTranscript() {
    this.loading = true; this.error = ''; this.txSummary = ''
    try {
      const result = await firstValueFrom(this.http.post<IngestResult>(`${API_BASE}/knowledge/artifacts/transcript`, {
        title: this.txTitle, content: this.txContent,
        source_type: this.txSourceType,
        author: this.txAuthor || 'unknown',
        tags: this.txTags.split(',').map(t => t.trim()).filter(Boolean)
      }))
      if (result.summary) this.txSummary = result.summary
      this.txTitle = ''; this.txAuthor = ''; this.txTags = ''; this.txContent = ''
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Transcript ingestion failed') }
    finally { this.loading = false }
  }

  startEditArtifact(a: Artifact) {
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
      }))
      this.editingArtifactId = ''
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Save failed') }
    finally { this.saving = false }
  }

  async deleteArtifact(id: string) {
    if (!confirm('Delete this artifact and all its extracted knowledge items?')) return
    this.deletingId = id; this.error = ''
    try {
      await firstValueFrom(this.http.delete(`${API_BASE}/knowledge/artifacts/${id}`))
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Delete failed') }
    finally { this.deletingId = '' }
  }

  async runCrossLink() {
    this.linkingCross = true; this.crossLinkCount = null
    try {
      const result = await firstValueFrom(this.http.post<CrossLinkResult>(`${API_BASE}/knowledge/link`, {}))
      this.crossLinkCount = result.links_created
      await this.loadKnowledge()
    } catch (e) { this.error = errorMessage(e, 'Cross-link failed') }
    finally { this.linkingCross = false }
  }
}
