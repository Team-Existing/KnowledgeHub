import { Routes } from '@angular/router'
import { inject } from '@angular/core'
import { Router } from '@angular/router'
import { adminGuard, authGuard } from './services/auth.guard'
import { AuthService } from './services/auth.service'

/** Decode the JWT payload without verifying the signature (client-side only). */
function jwtExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    // exp is Unix seconds; Date.now() is ms
    return typeof payload.exp === 'number' && payload.exp * 1000 < Date.now()
  } catch {
    return true  // malformed token → treat as expired
  }
}

const rootRedirect = () => {
  const auth = inject(AuthService)
  const router = inject(Router)
  const token = auth.token()
  const valid = !!token && !jwtExpired(token)
  if (!valid && token) {
    // Clear the stale token so authGuard and authInterceptor don't keep using it
    auth.logout()
  }
  return router.createUrlTree([valid ? '/knowledge' : '/onboarding'])
}

export const routes: Routes = [
  { path: '', canActivate: [rootRedirect], children: [] },
  { path: 'onboarding', loadComponent: () => import('./pages/onboarding/onboarding.component').then(m => m.OnboardingComponent) },
  { path: 'login', loadComponent: () => import('./pages/login/login.component').then(m => m.LoginComponent) },
  { path: 'knowledge', loadComponent: () => import('./pages/knowledge/knowledge.component').then(m => m.KnowledgeComponent), canActivate: [authGuard] },
  { path: 'knowledge/:id', loadComponent: () => import('./pages/knowledge-detail/knowledge-detail.component').then(m => m.KnowledgeDetailComponent), canActivate: [authGuard] },
  { path: 'review', loadComponent: () => import('./pages/review/review.component').then(m => m.ReviewComponent), canActivate: [authGuard] },
  { path: 'search', loadComponent: () => import('./pages/search/search.component').then(m => m.SearchComponent), canActivate: [authGuard] },
  { path: 'decisions', loadComponent: () => import('./pages/decisions/decisions.component').then(m => m.DecisionsComponent), canActivate: [authGuard] },
  { path: 'systems', loadComponent: () => import('./pages/systems/systems.component').then(m => m.SystemsComponent), canActivate: [authGuard] },
  { path: 'assistant', loadComponent: () => import('./pages/assistant/assistant.component').then(m => m.AssistantComponent), canActivate: [authGuard] },
  { path: 'learn', loadComponent: () => import('./pages/learn/learn.component').then(m => m.LearnComponent), canActivate: [authGuard] },
  { path: 'decision-copilot', loadComponent: () => import('./pages/copilot/copilot.component').then(m => m.CopilotComponent), canActivate: [authGuard] },
  { path: 'impact', loadComponent: () => import('./pages/impact/impact.component').then(m => m.ImpactComponent), canActivate: [authGuard] },
  { path: 'commitments', loadComponent: () => import('./pages/commitments/commitments.component').then(m => m.CommitmentsComponent), canActivate: [authGuard] },
  { path: 'knowledge-health', loadComponent: () => import('./pages/health/health.component').then(m => m.HealthComponent), canActivate: [authGuard] },
  { path: 'connectors', loadComponent: () => import('./pages/connectors/connectors.component').then(m => m.ConnectorsComponent), canActivate: [authGuard] },
  { path: 'graphrag', loadComponent: () => import('./pages/graphrag/graphrag.component').then(m => m.GraphragComponent), canActivate: [authGuard] },
  { path: 'settings/models', loadComponent: () => import('./pages/model-manager/model-manager.component').then(m => m.ModelManagerComponent), canActivate: [authGuard] },
  { path: 'playbooks', loadComponent: () => import('./pages/playbooks/playbooks.component').then(m => m.PlaybooksComponent), canActivate: [authGuard] },
  { path: 'sso/complete', loadComponent: () => import('./pages/sso-complete/sso-complete.component').then(m => m.SsoCompleteComponent) },
  { path: 'account', loadComponent: () => import('./pages/account/account.component').then(m => m.AccountComponent), canActivate: [authGuard] },
  { path: 'organizations', loadComponent: () => import('./pages/organizations/organizations.component').then(m => m.OrganizationsComponent), canActivate: [authGuard] },
  { path: 'join/:token', loadComponent: () => import('./pages/join/join.component').then(m => m.JoinComponent), canActivate: [authGuard] },
  { path: 'groups', loadComponent: () => import('./pages/groups/groups.component').then(m => m.GroupsComponent), canActivate: [authGuard] },
  { path: 'admin/users', loadComponent: () => import('./pages/admin-users/admin-users.component').then(m => m.AdminUsersComponent), canActivate: [adminGuard] },
  // an empty stop SpaceService.switchTo passes through, so the page you're on is rebuilt for the new space
  { path: 'switching-space', children: [] },
  { path: 'privacy-policy', loadComponent: () => import('./pages/privacy-policy/privacy-policy.component').then(m => m.PrivacyPolicyComponent) },
]
