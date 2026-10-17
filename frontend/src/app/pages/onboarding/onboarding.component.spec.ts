import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { provideRouter } from '@angular/router'
import { OnboardingComponent } from './onboarding.component'

const CATALOG = {
  ollamaReachable: true, anyInstalled: true, ramGb: 16, recommended: 'llama3.1:8b',
  models: [{ id: 'llama3.1:8b', name: 'Llama 3.1 8B', size: '4.7 GB', ramRequired: '8 GB+', minRamGb: 8,
             description: '', provider: 'ollama' as const, installed: true, active: false }],
}

describe('OnboardingComponent first account', () => {
  let fixture: ComponentFixture<OnboardingComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''
  const tick = () => new Promise(resolve => setTimeout(resolve))

  async function openAccountStep(setupTokenRequired: boolean) {
    fixture.detectChanges()                                     // ngOnInit → GET /models/catalog, /auth/setup
    http.expectOne('/models/catalog').flush(CATALOG)
    http.expectOne('/auth/setup').flush({ setup_token_required: setupTokenRequired, registration_open: true })
    await fixture.whenStable()
    const started = fixture.componentInstance.start()            // Get Started re-checks the catalog
    http.expectOne('/models/catalog').flush(CATALOG)
    await started
    fixture.detectChanges()
  }

  async function createAccount(expectedBody: object) {
    const c = fixture.componentInstance
    c.username = 'priya'; c.password = 'secret123'
    const done = c.createAccount()
    const register = http.expectOne('/auth/register')
    expect(register.request.body).toEqual(expectedBody)
    register.flush({ user_id: '1', role: 'admin' }, { status: 201, statusText: 'Created' })
    await tick()
    http.expectOne('/auth/token').flush({ access_token: 'x.y.z', token_type: 'bearer' })
    await tick()
    http.expectOne('/auth/me').flush({ id: '1', username: 'priya', role: 'admin', permissions: [], llm_model: null })
    await done
    expect(c.step).toBe('done')
  }

  beforeEach(async () => {
    localStorage.removeItem('kh_token')
    await TestBed.configureTestingModule({
      imports: [OnboardingComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents()
    fixture = TestBed.createComponent(OnboardingComponent)
    http = TestBed.inject(HttpTestingController)
  })

  afterEach(() => { http.verify(); localStorage.removeItem('kh_token') })

  it('asks a new server for the setup token and sends it', async () => {
    await openAccountStep(true)
    expect(text()).toContain('SETUP_TOKEN')
    fixture.componentInstance.setupToken = 'from-the-server'
    await createAccount({ username: 'priya', password: 'secret123', setup_token: 'from-the-server' })
  })

  it('creates the account without a token when none is needed', async () => {
    await openAccountStep(false)
    expect(text()).not.toContain('SETUP_TOKEN')
    await createAccount({ username: 'priya', password: 'secret123' })
  })
})
