import { TestBed } from '@angular/core/testing'
import { HttpClient, HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { Router } from '@angular/router'
import { authInterceptor, isApiRequest } from './auth.interceptor'
import { AuthService } from './auth.service'

describe('authInterceptor', () => {
  let http: HttpClient
  let backend: HttpTestingController
  let auth: AuthService
  let router: jasmine.SpyObj<Router>

  function setup(token: string | null) {
    if (token) localStorage.setItem('kh_token', token)
    else localStorage.removeItem('kh_token')
    router = jasmine.createSpyObj<Router>('Router', ['navigate'])
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: router },
      ],
    })
    http = TestBed.inject(HttpClient)
    backend = TestBed.inject(HttpTestingController)
    auth = TestBed.inject(AuthService)
  }

  afterEach(() => {
    backend.verify()
    localStorage.removeItem('kh_token')
  })

  it('adds the bearer token to API requests', () => {
    setup('tok123')
    http.get('/knowledge').subscribe()
    expect(backend.expectOne('/knowledge').request.headers.get('Authorization')).toBe('Bearer tok123')
  })

  it('sends no Authorization header when signed out', () => {
    setup(null)
    http.get('/knowledge').subscribe()
    expect(backend.expectOne('/knowledge').request.headers.has('Authorization')).toBeFalse()
  })

  it('never sends the token to another origin', () => {
    setup('tok123')
    http.get('https://example.com/data').subscribe()
    expect(backend.expectOne('https://example.com/data').request.headers.has('Authorization')).toBeFalse()
  })

  it('keeps an Authorization header the caller set explicitly', () => {
    setup('tok123')
    http.get('/knowledge', { headers: { Authorization: 'Bearer other' } }).subscribe()
    expect(backend.expectOne('/knowledge').request.headers.get('Authorization')).toBe('Bearer other')
  })

  it('uses the current token, not the one at startup', () => {
    setup(null)
    localStorage.setItem('kh_token', 'late')
    ;(auth as unknown as { _token: { set(v: string): void } })._token.set('late')
    http.get('/knowledge').subscribe()
    expect(backend.expectOne('/knowledge').request.headers.get('Authorization')).toBe('Bearer late')
  })

  it('signs out and redirects to /login on 401 from the API', () => {
    setup('expired')
    let err: unknown
    http.get('/knowledge').subscribe({ error: e => (err = e) })
    backend.expectOne('/knowledge').flush({ detail: 'Invalid or expired token' }, { status: 401, statusText: 'Unauthorized' })
    expect(auth.isLoggedIn()).toBeFalse()
    expect(localStorage.getItem('kh_token')).toBeNull()
    expect(router.navigate).toHaveBeenCalledWith(['/login'], { queryParams: { expired: 1 } })
    expect(err instanceof HttpErrorResponse && err.status === 401).toBeTrue()  // still reaches the caller
  })

  it('does not sign out on a 401 from the login endpoint (wrong password)', () => {
    setup('tok123')
    http.post('/auth/token', 'username=a&password=b').subscribe({ error: () => {} })
    backend.expectOne('/auth/token').flush({ detail: 'Invalid credentials' }, { status: 401, statusText: 'Unauthorized' })
    expect(auth.isLoggedIn()).toBeTrue()
    expect(router.navigate).not.toHaveBeenCalled()
  })

  it('leaves the session alone on other errors', () => {
    setup('tok123')
    http.get('/knowledge').subscribe({ error: () => {} })
    backend.expectOne('/knowledge').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' })
    expect(auth.isLoggedIn()).toBeTrue()
    expect(router.navigate).not.toHaveBeenCalled()
  })
})

describe('isApiRequest', () => {
  it('treats relative URLs as API requests and absolute ones as external', () => {
    expect(isApiRequest('/knowledge')).toBeTrue()
    expect(isApiRequest('knowledge/search?q=x')).toBeTrue()
    expect(isApiRequest('https://example.com/x')).toBeFalse()
    expect(isApiRequest('http://localhost:11434/api/tags')).toBeFalse()
  })
})
