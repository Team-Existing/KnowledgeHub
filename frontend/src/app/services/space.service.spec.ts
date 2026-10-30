import { Component } from '@angular/core'
import { TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { NavigationEnd, Router, provideRouter } from '@angular/router'
import { SpaceService } from './space.service'
import { routes } from '../app.routes'

@Component({ template: 'page' })
class Page {}

describe('SpaceService.switchTo', () => {
  let router: Router
  let ended: string[]

  beforeEach(() => {
    localStorage.removeItem('kh_space')
    // the app's real 'switching-space' stop, with stand-in pages for the hub and search
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(),
        provideRouter([...routes.filter(r => r.path === 'switching-space'),
                       { path: 'knowledge', component: Page }, { path: 'search', component: Page }]),
      ],
    })
    router = TestBed.inject(Router)
    ended = []
    router.events.subscribe(e => { if (e instanceof NavigationEnd) ended.push(e.urlAfterRedirects) })
  })

  afterEach(() => localStorage.removeItem('kh_space'))

  it('re-opens the hub for the new space', async () => {
    await router.navigateByUrl('/knowledge')
    ended = []
    await TestBed.inject(SpaceService).switchTo('group-1')
    // a real navigation away and back: going via '/' (which redirects to /knowledge) was ignored on the hub
    expect(ended).toEqual(['/switching-space', '/knowledge'])
    expect(router.url).toBe('/knowledge')
    expect(TestBed.inject(SpaceService).activeId()).toBe('group-1')
  })

  it('returns to the page it was on', async () => {
    await router.navigateByUrl('/search?q=x')
    ended = []
    await TestBed.inject(SpaceService).switchTo('group-1')
    expect(ended).toEqual(['/switching-space', '/search?q=x'])
  })
})

describe('SpaceService switcher', () => {
  it('lists each organization followed by your groups in it', async () => {
    localStorage.removeItem('kh_space')
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] })
    const service = TestBed.inject(SpaceService)
    const http = TestBed.inject(HttpTestingController)
    const loaded = service.load()
    http.expectOne('/spaces').flush([
      { id: 'u1', kind: 'personal', name: 'My space', role: 'owner' },
      { id: 'org_a', kind: 'organization', name: 'Acme', role: 'owner', member_count: 3 },
      { id: 'grp_1', kind: 'group', name: 'Platform', role: 'admin', org_id: 'org_a', org_name: 'Acme' },
      { id: 'org_b', kind: 'organization', name: 'Beta', role: 'member', member_count: 2 },
    ])
    http.expectOne('/invitations').flush([])
    await loaded
    expect(service.personal().map(s => s.id)).toEqual(['u1'])
    expect(service.organizations().map(o => [o.org.id, o.groups.map(g => g.id)]))
      .toEqual([['org_a', ['grp_1']], ['org_b', []]])
    localStorage.removeItem('kh_space')
  })
})
