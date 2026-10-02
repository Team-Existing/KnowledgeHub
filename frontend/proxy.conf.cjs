// Dev-server proxy to the FastAPI backend.
// Some API prefixes (/knowledge, /connectors, /admin, /groups) are also Angular routes, so a browser
// page load (Accept: text/html) must fall through to the SPA instead of the backend.
const target = 'http://localhost:8000'

const bypass = (req) => {
  if (req.method === 'GET' && (req.headers.accept || '').includes('text/html')) {
    return '/index.html'
  }
  return undefined
}

const entry = { target, secure: false, bypass }

module.exports = {
  '/auth': entry,
  '/admin': entry,
  '/spaces': entry,
  '/groups': entry,
  '/invitations': entry,
  '/knowledge': entry,
  '/connectors': entry,
  '/models': entry,
  '/health': entry,
}
