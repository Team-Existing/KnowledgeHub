import { Injectable, computed, signal } from '@angular/core'
import { HttpClient } from '@angular/common/http'
import { Router } from '@angular/router'
import { firstValueFrom } from 'rxjs'
import { API_BASE } from './auth.service'
import { Invitation, SpaceInfo } from '../models/api'

const STORAGE_KEY = 'kh_space'

/**
 * The active space (personal or a group). authInterceptor sends its id as the
 * X-Space header on every API call, so every page shows that space's data.
 */
@Injectable({ providedIn: 'root' })
export class SpaceService {
  private _spaces = signal<SpaceInfo[]>([])
  private _activeId = signal<string | null>(readStored())
  private _invitations = signal<Invitation[]>([])

  readonly spaces = this._spaces.asReadonly()
  readonly invitations = this._invitations.asReadonly()
  /** Header value; null until spaces load (the server then uses the personal space). */
  readonly activeId = this._activeId.asReadonly()
  readonly active = computed(() =>
    this._spaces().find(s => s.id === this._activeId()) ?? this._spaces().find(s => s.kind === 'personal') ?? null)

  constructor(private http: HttpClient, private router: Router) {}

  async load(): Promise<void> {
    try {
      const [spaces, invitations] = await Promise.all([
        firstValueFrom(this.http.get<SpaceInfo[]>(`${API_BASE}/spaces`)),
        firstValueFrom(this.http.get<Invitation[]>(`${API_BASE}/invitations`)),
      ])
      this._spaces.set(spaces)
      this._invitations.set(invitations)
      // a stored group you were removed from (or another account's choice) falls back to your own space
      if (!spaces.some(s => s.id === this._activeId())) this.setActive(spaces[0]?.id ?? null)
    } catch { /* signed out or server down: keep what we have */ }
  }

  /** Switch spaces and re-open the current page so it loads the new space's data. */
  async switchTo(id: string): Promise<void> {
    if (id === this._activeId()) return
    this.setActive(id)
    const url = this.router.url.startsWith('/knowledge/') ? '/knowledge' : this.router.url   // items don't cross spaces
    await this.router.navigateByUrl('/', { skipLocationChange: true })
    await this.router.navigateByUrl(url)
  }

  /** Called when the server says we're no longer in the active group. */
  async fallBackToPersonal(): Promise<void> {
    this.setActive(null)
    await this.load()
  }

  reset(): void {
    this.setActive(null)
    this._spaces.set([]); this._invitations.set([])
  }

  private setActive(id: string | null): void {
    this._activeId.set(id)
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id); else localStorage.removeItem(STORAGE_KEY)
    } catch { /* storage unavailable: the choice just won't persist */ }
  }
}

function readStored(): string | null {
  try { return localStorage.getItem(STORAGE_KEY) } catch { return null }
}
