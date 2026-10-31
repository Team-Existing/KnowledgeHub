import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { SlackSettingsComponent } from './slack-settings.component'

describe('SlackSettingsComponent', () => {
  let fixture: ComponentFixture<SlackSettingsComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SlackSettingsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents()
    fixture = TestBed.createComponent(SlackSettingsComponent)
    http = TestBed.inject(HttpTestingController)
    fixture.detectChanges()
  })

  afterEach(() => http.verify())

  it('adds a Slack app with its signing secret and shows the request URL', async () => {
    http.expectOne('/knowledge/slack').flush([])
    await fixture.whenStable()
    fixture.detectChanges()
    expect(text()).toContain('Connect a Slack workspace')
    const c = fixture.componentInstance
    c.name = 'Acme'; c.secret = '8f742231b10e8888abcd'
    const adding = c.add()
    const post = http.expectOne(r => r.method === 'POST' && r.url === '/knowledge/slack')
    expect(post.request.body).toEqual({ name: 'Acme', signing_secret: '8f742231b10e8888abcd' })
    post.flush({ id: 'slk_1', name: 'Acme', request_url: 'https://hub/slack/slk_1/command', command: '/decisions',
                 created_at: '', created_by_name: 'ana', last_used_at: null })
    await new Promise(resolve => setTimeout(resolve))
    http.expectOne(r => r.method === 'GET' && r.url === '/knowledge/slack').flush([
      { id: 'slk_1', name: 'Acme', request_url: 'https://hub/slack/slk_1/command', command: '/decisions',
        created_at: '', created_by_name: 'ana', last_used_at: null }])
    await adding
    fixture.detectChanges()
    expect(c.secret).toBe('')
    const url = (fixture.nativeElement as HTMLElement).querySelector('input[aria-label="Request URL"]') as HTMLInputElement
    expect(url.value).toBe('https://hub/slack/slk_1/command')
    expect(text()).toContain('not used yet')
  })
})
