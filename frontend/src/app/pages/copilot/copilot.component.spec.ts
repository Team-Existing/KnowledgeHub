import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router'
import { CopilotComponent } from './copilot.component'

const GITHUB = { id: 'conn_1', kind: 'github', name: 'payments-api', config: { repo_url: 'https://github.com/acme/payments-api' },
                 copilot: { enabled: false, has_secret: false } }

describe('CopilotComponent', () => {
  let fixture: ComponentFixture<CopilotComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''
  const settle = async () => { await new Promise(resolve => setTimeout(resolve)); await fixture.whenStable(); fixture.detectChanges() }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CopilotComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting(),
                  { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({ connector: 'conn_1' }) } } }],
    }).compileComponents()
    fixture = TestBed.createComponent(CopilotComponent)
    http = TestBed.inject(HttpTestingController)
    fixture.detectChanges()
    http.expectOne('/connectors').flush([{ id: 'conn_2', kind: 'folder', name: 'x', config: {} }, GITHUB])
    await settle()
    http.expectOne('/connectors/conn_1/copilot').flush({ enabled: false, has_secret: false, webhook_url: 'https://hub/copilot/github/conn_1/webhook' })
    http.expectOne('/knowledge/copilot/runs').flush([])
    await settle()
  })

  afterEach(() => http.verify())

  it('previews a pull request without posting, then posts on request', async () => {
    expect(fixture.componentInstance.connectors.map(c => c.id)).toEqual(['conn_1'])     // GitHub, Jira, Linear only
    fixture.componentInstance.ref = '7'
    const analyzing = fixture.componentInstance.analyze()
    const preview = http.expectOne('/knowledge/copilot/preview')
    expect(preview.request.body).toEqual({ connector_id: 'conn_1', ref: '7' })
    preview.flush({ target: { ref: '7', title: 'Move payments to MongoDB', url: 'https://github.com/acme/payments-api/pull/7',
                              noun: 'pull request', skip: null },
                    files: 1, relevant: true, comment: '<!-- knowledge-hubs-copilot -->\n### Knowledge Hubs', report: {} })
    await analyzing
    fixture.detectChanges()
    expect(text()).toContain('a webhook would comment')
    expect(text()).toContain('### Knowledge Hubs')

    const posting = fixture.componentInstance.post()
    http.expectOne('/knowledge/copilot/post').flush({ id: 'cop_1', status: 'posted' })
    await new Promise(resolve => setTimeout(resolve))
    http.expectOne('/knowledge/copilot/runs').flush([])
    await posting
    expect(fixture.componentInstance.notice).toBe('Posted the comment on the pull request.')
  })

  it('shows the webhook secret once, when it is created', async () => {
    const enabling = fixture.componentInstance.setEnabled(true)
    http.expectOne(r => r.method === 'PUT' && r.url === '/connectors/conn_1/copilot').flush({
      enabled: true, has_secret: true, webhook_url: 'https://hub/copilot/github/conn_1/webhook', webhook_secret: 's3cret' })
    await enabling
    fixture.detectChanges()
    expect(text()).toContain('shown only now')
    expect(fixture.componentInstance.secret).toBe('s3cret')
  })

  it('asks Linear users for the signing secret Linear created', async () => {
    const c = fixture.componentInstance
    c.connectors = [...c.connectors, { id: 'conn_3', kind: 'linear', name: 'Linear', config: { api_url: 'https://api.linear.app/graphql' } } as never]
    const choosing = c.choose('conn_3')
    http.expectOne('/connectors/conn_3/copilot').flush({ enabled: false, has_secret: false, webhook_url: 'https://hub/copilot/linear/conn_3/webhook' })
    await choosing
    fixture.detectChanges()
    expect(text()).toContain('paste the signing secret Linear shows')
    c.linearSecret = 'lin_wh_123'
    const saving = c.setEnabled(true)
    const put = http.expectOne('/connectors/conn_3/copilot')
    expect(put.request.body).toEqual({ enabled: true, rotate_secret: false, webhook_secret: 'lin_wh_123' })
    put.flush({ enabled: true, has_secret: true, webhook_url: 'https://hub/copilot/linear/conn_3/webhook' })
    await saving
    expect(c.linearSecret).toBe('')
  })
})
