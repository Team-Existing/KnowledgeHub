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
    http.expectOne('/connectors').flush([{ id: 'conn_2', kind: 'jira', name: 'x', config: {} }, GITHUB])
    await settle()
    http.expectOne('/connectors/conn_1/copilot').flush({ enabled: false, has_secret: false, webhook_url: 'https://hub/copilot/github/conn_1/webhook' })
    http.expectOne('/knowledge/copilot/runs').flush([])
    await settle()
  })

  afterEach(() => http.verify())

  it('previews a pull request without posting, then posts on request', async () => {
    expect(fixture.componentInstance.connectors.map(c => c.id)).toEqual(['conn_1'])     // GitHub connectors only
    fixture.componentInstance.prNumber = 7
    const analyzing = fixture.componentInstance.analyze()
    const preview = http.expectOne('/knowledge/copilot/preview')
    expect(preview.request.body).toEqual({ connector_id: 'conn_1', pr_number: 7 })
    preview.flush({ pr: { number: 7, title: 'Move payments to MongoDB', url: 'https://github.com/acme/payments-api/pull/7' },
                    files: 1, relevant: true, comment: '<!-- knowledge-hubs-copilot -->\n### Knowledge Hubs', report: {} })
    await analyzing
    fixture.detectChanges()
    expect(text()).toContain('the webhook would comment')
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
})
