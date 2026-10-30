import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE, AuthService } from '../../services/auth.service'
import { SpaceService } from '../../services/space.service'
import { errorMessage } from '../../services/http-error'
import { CreatedInvite, Organization, OrgDomain, OrgInvite, OrgMember, OrgRole } from '../../models/api'

/**
 * Your organizations: create one, and for the selected one see its members,
 * change roles (owners), remove people and manage invite links (owners and admins).
 */
@Component({
  selector: 'app-organizations',
  imports: [CommonModule, FormsModule],
  templateUrl: './organizations.component.html'
})
export class OrganizationsComponent implements OnInit {
  orgs: Organization[] = []
  selected: Organization | null = null
  members: OrgMember[] = []
  invites: OrgInvite[] = []
  domains: OrgDomain[] = []
  /** The link just created: its token is shown only now */
  created: CreatedInvite | null = null
  copied = false

  newName = ''; renameTo = ''
  inviteRole: 'member' | 'admin' = 'member'; inviteDays = 7; inviteMaxUses: number | null = null
  error = ''; notice = ''; busy = false

  readonly roles: OrgRole[] = ['owner', 'admin', 'member']

  constructor(private http: HttpClient, public spaces: SpaceService, public auth: AuthService) {}

  async ngOnInit() { await this.loadOrgs() }

  get isOwner(): boolean { return this.selected?.role === 'owner' }
  get canManage(): boolean { return this.selected?.role === 'owner' || this.selected?.role === 'admin' }
  get ownerCount(): number { return this.members.filter(m => m.role === 'owner').length }
  get joinUrl(): string { return this.created ? `${location.origin}/join/${this.created.token}` : '' }

  /** Admins remove members; owners remove anyone but the last owner; nobody removes themselves here */
  canRemove(m: OrgMember): boolean {
    if (m.user_id === this.auth.me()?.id) return false
    if (this.isOwner) return m.role !== 'owner' || this.ownerCount > 1
    return this.selected?.role === 'admin' && m.role === 'member'
  }

  async loadOrgs() {
    try {
      this.orgs = await firstValueFrom(this.http.get<Organization[]>(`${API_BASE}/organizations`))
      if (this.selected) this.selected = this.orgs.find(o => o.id === this.selected!.id) ?? null
      if (!this.selected && this.orgs.length === 1) await this.select(this.orgs[0])
    } catch (e) { this.error = errorMessage(e, 'Could not load organizations') }
  }

  async select(org: Organization) {
    this.selected = org; this.renameTo = org.name
    this.created = null; this.error = ''; this.notice = ''
    await this.loadDetails()
  }

  async loadDetails() {
    if (!this.selected) return
    const id = this.selected.id
    try {
      // your role may have changed since the list loaded; it decides what's offered below
      this.selected = await firstValueFrom(this.http.get<Organization>(`${API_BASE}/organizations/${id}`))
      const [members, invites] = await Promise.all([
        firstValueFrom(this.http.get<OrgMember[]>(`${API_BASE}/organizations/${id}/members`)),
        this.canManage ? firstValueFrom(this.http.get<OrgInvite[]>(`${API_BASE}/organizations/${id}/invites`))
          : Promise.resolve([] as OrgInvite[]),
      ])
      this.members = members; this.invites = invites
      this.domains = this.canManage
        ? await firstValueFrom(this.http.get<OrgDomain[]>(`${API_BASE}/organizations/${id}/domains`)) : []
    } catch (e) { this.error = errorMessage(e, 'Could not load the organization') }
  }

  async create() {
    await this.run(async () => {
      const org = await firstValueFrom(this.http.post<Organization>(`${API_BASE}/organizations`, { name: this.newName }))
      this.newName = ''
      this.notice = `Created “${org.name}”. You're its owner: create an invite link below and send it to your team.`
      await Promise.all([this.loadOrgs(), this.spaces.load()])
      const found = this.orgs.find(o => o.id === org.id)
      if (found) await this.select(found)
    }, 'Could not create the organization')
  }

  async rename() {
    await this.run(async () => {
      await firstValueFrom(this.http.patch(`${API_BASE}/organizations/${this.selected!.id}`, { name: this.renameTo }))
      await Promise.all([this.loadOrgs(), this.spaces.load()])
    }, 'Could not rename')
  }

  async setRole(m: OrgMember, role: OrgRole) {
    if (role === m.role) return
    await this.run(async () => {
      await firstValueFrom(this.http.patch(`${API_BASE}/organizations/${this.selected!.id}/members/${m.user_id}`, { role }))
      await Promise.all([this.loadOrgs(), this.loadDetails()])
    }, 'Could not change the role')
    if (this.error) await this.loadDetails()       // put the select back
  }

  async remove(m: OrgMember) {
    if (!confirm(`Remove ${m.username} from ${this.selected!.name}? They also leave its groups. What they added stays.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/organizations/${this.selected!.id}/members/${m.user_id}`))
      await Promise.all([this.loadOrgs(), this.loadDetails()])
    }, 'Could not remove')
  }

  async createInvite() {
    await this.run(async () => {
      this.created = await firstValueFrom(this.http.post<CreatedInvite>(`${API_BASE}/organizations/${this.selected!.id}/invites`, {
        role: this.inviteRole, expires_in_days: this.inviteDays, max_uses: this.inviteMaxUses || null,
      }))
      this.copied = false
      await this.loadDetails()
    }, 'Could not create the invite link')
  }

  async copyLink() {
    try { await navigator.clipboard.writeText(this.joinUrl); this.copied = true }
    catch { this.error = 'Could not copy; select the link and copy it yourself' }
  }

  async revoke(invite: OrgInvite) {
    if (!confirm('Revoke this invite link? Anyone who has it can no longer join with it.')) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/organizations/${this.selected!.id}/invites/${invite.id}`))
      if (this.created?.id === invite.id) this.created = null
      await this.loadDetails()
    }, 'Could not revoke the link')
  }

  /** Your verified email's domain: the only one you can claim */
  get myDomain(): string | null {
    const email = this.auth.me()?.email
    return email ? email.split('@')[1] : null
  }

  async addDomain() {
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/organizations/${this.selected!.id}/domains`, { domain: this.myDomain }))
      this.notice = `Anyone who signs in with a verified @${this.myDomain} address now joins ${this.selected!.name}.`
      await this.loadDetails()
    }, 'Could not add the domain')
  }

  async removeDomain(d: OrgDomain) {
    if (!confirm(`Stop adding people with @${d.domain} addresses? Those who already joined stay.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/organizations/${this.selected!.id}/domains/${encodeURIComponent(d.domain)}`))
      await this.loadDetails()
    }, 'Could not remove the domain')
  }

  async leave() {
    if (!confirm(`Leave ${this.selected!.name}? You'll lose access to its space and its groups.`)) return
    await this.run(async () => {
      await firstValueFrom(this.http.post(`${API_BASE}/organizations/${this.selected!.id}/leave`, {}))
      await this.leftSelected()
    }, 'Could not leave')
  }

  async deleteOrg() {
    const name = this.selected!.name
    if (prompt(`This deletes ${name}, all its groups and everything in them, for every member. It cannot be undone.\n\nType the organization's name to confirm:`) !== name) return
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`${API_BASE}/organizations/${this.selected!.id}`))
      await this.leftSelected()
    }, 'Could not delete the organization')
  }

  open(org: Organization) { this.spaces.switchTo(org.id) }

  private async leftSelected() {
    const id = this.selected!.id
    const active = this.spaces.active()
    this.selected = null; this.members = []; this.invites = []; this.created = null
    if (active && (active.id === id || active.org_id === id)) await this.spaces.fallBackToPersonal()
    await Promise.all([this.loadOrgs(), this.spaces.load()])
  }

  private async run(action: () => Promise<void>, failure: string) {
    this.busy = true; this.error = ''; this.notice = ''
    try { await action() } catch (e) { this.error = errorMessage(e, failure) }
    finally { this.busy = false }
  }
}
