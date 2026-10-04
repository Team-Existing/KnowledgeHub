import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { HttpClient } from '@angular/common/http'
import { firstValueFrom } from 'rxjs'
import { API_BASE, AuthService } from '../../services/auth.service'
import { errorMessage } from '../../services/http-error'
import { AdminUser, RetentionReport, RetentionStatus, Role } from '../../models/api'
import { USERNAME_RULES, isValidUsername } from '../../services/usernames'

@Component({
  selector: 'app-admin-users',
  imports: [CommonModule, FormsModule],
  templateUrl: './admin-users.component.html'
})
export class AdminUsersComponent implements OnInit {
  readonly roles: Role[] = ['admin', 'member']
  users: AdminUser[] = []
  error = ''; notice = ''; busyId = ''

  newUsername = ''; newPassword = ''; newRole: Role = 'member'; creating = false
  readonly usernameRules = USERNAME_RULES
  get usernameOk(): boolean { return isValidUsername(this.newUsername) }

  constructor(private http: HttpClient, public auth: AuthService) {}

  retention: RetentionStatus | null = null
  running = false

  ngOnInit() { this.load() }

  async load() {
    try {
      const [users, retention] = await Promise.all([
        firstValueFrom(this.http.get<AdminUser[]>(`${API_BASE}/admin/users`)),
        firstValueFrom(this.http.get<RetentionStatus>(`${API_BASE}/admin/retention`)),
      ])
      this.retention = retention
      this.users = users
    } catch (e) { this.error = errorMessage(e, 'Could not load users') }
  }

  get dueCount(): number {
    return (this.retention?.due.users.length ?? 0) + (this.retention?.due.groups.length ?? 0)
  }

  async runRetention() {
    if (!confirm(`Permanently delete the ${this.dueCount} inactive account(s)/group(s) listed, with all their data?`)) return
    this.running = true; this.error = ''; this.notice = ''
    try {
      const r = await firstValueFrom(this.http.post<RetentionReport>(`${API_BASE}/admin/retention/run`, {}))
      this.notice = `Deleted ${r.users_deleted.length} account(s) and ${r.groups_deleted.length} group(s)` +
        (r.skipped.length ? `; skipped ${r.skipped.length}.` : '.')
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not run the clean-up') }
    finally { this.running = false }
  }

  async setRole(user: AdminUser, role: string) {
    if (role === user.role) return
    const self = user.id === this.auth.me()?.id
    if (self && role !== 'admin' && !confirm('Remove your own admin rights? You will lose access to this page.')) {
      await this.load()   // reset the select
      return
    }
    this.busyId = user.id; this.error = ''; this.notice = ''
    try {
      await firstValueFrom(this.http.patch(`${API_BASE}/admin/users/${user.id}`, { role }))
      this.notice = `${user.username} is now ${role === 'admin' ? 'an admin' : 'a member'}.`
      if (self) await this.auth.loadMe()
      await this.load()
    } catch (e) {
      this.error = errorMessage(e, 'Could not change the role')
      await this.load()
    } finally { this.busyId = '' }
  }

  async create() {
    this.creating = true; this.error = ''; this.notice = ''
    try {
      await firstValueFrom(this.http.post(`${API_BASE}/admin/users`,
        { username: this.newUsername, password: this.newPassword, role: this.newRole }))
      this.notice = `Account ${this.newUsername} created.`
      this.newUsername = ''; this.newPassword = ''; this.newRole = 'member'
      await this.load()
    } catch (e) { this.error = errorMessage(e, 'Could not create the account') }
    finally { this.creating = false }
  }
}
