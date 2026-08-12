import { Component } from '@angular/core'
import { RouterLink } from '@angular/router'

@Component({
  selector: 'app-privacy-policy',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="privacy-shell">
      <div class="privacy-content">
        <a routerLink="/knowledge" class="back-link">← Back to hub</a>
        <h1>Data Privacy &amp; What Leaves Your Device</h1>
        <p class="lead">Knowledge Hubs is local-first. Nothing leaves your machine unless you explicitly enable a cloud provider.</p>

        <div class="card">
          <h2>🟢 Local Mode (Default)</h2>
          <p>All processing runs on your machine using <strong>sentence-transformers</strong> for embeddings and <strong>Ollama</strong> for LLM inference.</p>
          <ul>
            <li>Text you ingest is stored only in your local SQLite database and Neo4j instance</li>
            <li>Embeddings are computed locally — no vectors are sent externally</li>
            <li>GraphRAG queries, extraction, and summarisation all run on-device</li>
            <li>The provider badge shows <strong>🟢 Local</strong> on every AI-powered screen</li>
          </ul>
          <div class="highlight">✓ In local mode, zero bytes of your content leave this device.</div>
        </div>



        <div class="card">
          <h2>🗄 What Is Stored Locally</h2>
          <ul>
            <li><strong>SQLite</strong> — artifacts, knowledge items, embeddings, relationships, query logs</li>
            <li><strong>Neo4j</strong> (when running) — graph nodes, edges, and vector index</li>
            <li><strong>Browser localStorage</strong> — JWT auth token and workspace ID only</li>
            <li><strong>sessionStorage</strong> — GraphRAG chat history (cleared when the tab closes)</li>
          </ul>
        </div>

        <div class="card">
          <h2>🔑 Authentication</h2>
          <ul>
            <li>Passwords are hashed with bcrypt — never stored in plaintext</li>
            <li>JWT tokens expire after 8 hours and are validated on every route navigation</li>
            <li>Tokens are stored in <code>localStorage</code> and cleared on logout or expiry</li>
          </ul>
        </div>

        <div class="card">
          <h2>📦 Model Downloads</h2>
          <ul>
            <li>Models are pulled from the Ollama registry — only the model binary is downloaded</li>
            <li>No user content is sent during model download</li>
            <li>Models are stored on your machine and run entirely offline after download</li>
          </ul>
        </div>

        <div class="card">
          <h2>🛡 Your Rights</h2>
          <ul>
            <li>You own all data you ingest — delete any artifact or item at any time via the UI or API</li>
            <li>Full workspace export is available via <code>GET /knowledge/okf/export</code></li>
            <li>No analytics, telemetry, or third-party tracking are included in this application</li>
          </ul>
        </div>

        <p class="footer-note">
          For questions about data handling, refer to the
          <a href="https://aws.amazon.com/compliance/data-privacy-faq/" target="_blank" rel="noopener">AWS Data Privacy FAQ</a>.
        </p>
      </div>
    </div>
  `,
  styles: [`
    .privacy-shell { max-width: 760px; margin: 0 auto; padding: 2rem; }
    .back-link {
      display: inline-block; color: #667eea; text-decoration: none;
      font-weight: 500; font-size: 0.875rem; margin-bottom: 1.25rem;
    }
    .back-link:hover { text-decoration: underline; }

    h1 { font-size: 1.6rem; color: #1f2933; margin-bottom: 0.5rem; }
    .lead { color: #667085; font-size: 0.95rem; margin-bottom: 1.75rem; line-height: 1.6; }

    .card {
      background: rgba(255,255,255,0.97);
      border: 1px solid #e5ecea;
      border-radius: 10px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1rem;
      box-shadow: 0 2px 8px rgba(102,126,234,0.06);
    }
    h2 { font-size: 1rem; color: #1f2933; margin: 0 0 0.75rem; }
    p { color: #475467; font-size: 0.875rem; line-height: 1.6; margin: 0.5rem 0; }
    ul { margin: 0.5rem 0 0.5rem 1.25rem; padding: 0; }
    li { color: #475467; font-size: 0.875rem; line-height: 1.7; }
    a { color: #667eea; text-decoration: none; }
    a:hover { text-decoration: underline; }
    code {
      background: #f2f4f7; padding: 0.1rem 0.35rem;
      border-radius: 3px; font-size: 0.8rem; color: #344054;
    }

    .highlight {
      background: #f0fdf4; border-left: 3px solid #16a34a;
      padding: 0.65rem 1rem; border-radius: 4px;
      font-size: 0.85rem; color: #15803d; margin-top: 0.75rem;
    }
    .warning {
      background: #fff8e1; border-left: 3px solid #f59e0b;
      padding: 0.65rem 1rem; border-radius: 4px;
      font-size: 0.85rem; color: #92400e; margin-top: 0.75rem;
    }

    .footer-note {
      color: #667085; font-size: 0.8rem; text-align: center;
      margin-top: 1.5rem; padding-top: 1rem;
      border-top: 1px solid #e5ecea;
    }
  `],
})
export class PrivacyPolicyComponent {}
