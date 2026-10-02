import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { Subject, debounceTime, firstValueFrom } from 'rxjs'
import { API_BASE, AuthService } from '../../services/auth.service'
import { SpaceService } from '../../services/space.service'
import { errorMessage } from '../../services/http-error'
import { Candidate, Group, GroupMember } from '../../models/api'

@Component({
  selector: 'app-groups',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './groups.component.html',
})
export class GroupsComponent implements OnInit {
  groups: Group[] = []
  selected: Group | null = null
  members: GroupMember[] = []
  candidates: Candidate[] = []
  query = ''
  newName = ''
  renameTo = ''
  error = ''; notice = ''; busy = false

  private search$ = new Subject<string>()

  constructor(private http: HttpClient, public spaces: SpaceService, public auth: AuthService) {
    this.search$.pipe(debounceTime(250)).subscribe(q => this.search(q))
  }

  async ngOnInit() {
    await Promise.all([this.loadGroups(), this.spaces.load()])
  }

  get isAdmin(): boolean { return this.selected?.role === 'admin' }
  get active(): GroupMember[] { return this.members.filter(m => m.status === 'active') }
  get pending(): GroupMember[] { return this.members.filter(m => m.status !== 'active') }

  async loadGroups() {
    try {
      this.groups = await firstValueFrom(this.http.get<Group[]>(`${API_BASE}/groups`))
      if (this.selected) this.selected = this.groups.find(g => g.id === this.selected!.id) ?? null
    } catch (e) { this.error = errorMessage(e, 'Could not load groups') }
  }

  async select(group: Group) {
    this.selected = group; this.renameTo = group.name
    this.query = ''; this.candidates = []; this.error = ''; this.notice = ''
    await this.loadMembers()
  }

  async loadMembers() {
    if (!this.selected) return
    try {
      this.members = await firstValueFrom(this.http.get<GroupMember[]>(`${API_BASE}/groups/${this.selected.id}/members`))
    } catch (e) { this.error = errorMessage(e, 'Could not load members') }
  }

  async create() {
    await this.run(async () => {
      const g = await firstValueFrom(this.http.post<Group>(`${API_BASE}/groups`, { name: this.newName }))
      this.newName = ''
      this.notice = `Created “${g.name}”. You're its admin — search for people below to invite them.`
      await Promise.all([this.loadGroups(), this.spaces.load()])
      const created = this.groups.find(x => x.id === g.id)
      if (created) await this.select(created)
    }, 'Could not create the group')
  }

  onQuery(q: string) { this.search$.next(q) }

  private async search(q: string) {
    if (!this.selected || !this.isAdmin || !q.trim()) { this.candidates = []; return }
    try {
      this.candidates = await firstValueFrom(this.http.get<Candidate[]>(
        `${API_BASE}/groups/${this.selected.id}/candidates`, { params: { q: q.trim() } }))
    } catch (e) { this.error = errorMessage(e, 'Search failed') }
  }

  canInvite(c: Candidate): boolean { return c.status !== 'active' && c.status !== 'invited' }

  async invite(c: Candidate) {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/groups/${this.selected!.id}/invitations`, { user_id: c.id }))
      this.notice = `Invited ${c.username}. They'll see the invitation under Groups.`
      await Promise.all([this.loadMembers(), this.search(this.query)])
    }, 'Could not send the invitation')
  }

  async remove(m: GroupMember) {
    const what = m.status === 'active' ? `Remove ${m.username} from the group? What they added stays.` : `Cancel ${m.username}'s invitation?`
    if (!confirm(what)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/groups/${this.selected!.id}/members/${m.user_id}`))
      await Promise.all([this.loadMembers(), this.loadGroups()])
    }, 'Could not remove')
  }

  async rename() {
    await this.run(async () => {
      await firstValueFrom(this.http.patch(`${API_BASE}/groups/${this.selected!.id}`, { name: this.renameTo }))
      await Promise.all([this.loadGroups(), this.spaces.load()])
    }, 'Could not rename')
  }

  async deleteGroup() {
    if (!confirm(`Delete “${this.selected!.name}” and everything in it, for every member? This cannot be undone.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/groups/${this.selected!.id}`))
      await this.leftSelected()
    }, 'Could not delete the group')
  }

  async leave() {
    if (!confirm(`Leave “${this.selected!.name}”? You'll lose access to its space.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/groups/${this.selected!.id}/leave`, {}))
      await this.leftSelected()
    }, 'Could not leave the group')
  }

  async respond(groupId: string, accept: boolean) {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/invitations/${groupId}/${accept ? 'accept' : 'decline'}`, {}))
      await Promise.all([this.loadGroups(), this.spaces.load()])
      if (accept) this.notice = 'Joined. Switch to the group with the space selector at the top.'
    }, 'Could not answer the invitation')
  }

  open(group: Group) { this.spaces.switchTo(group.id) }

  private async leftSelected() {
    const id = this.selected!.id
    this.selected = null; this.members = []
    if (this.spaces.activeId() === id) await this.spaces.fallBackToPersonal()
    await Promise.all([this.loadGroups(), this.spaces.load()])
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''; this.notice = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
