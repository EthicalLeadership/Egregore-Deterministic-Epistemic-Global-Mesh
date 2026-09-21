/**
 * ANCHORUM Desktop — Dossiers Panel
 * Lists legal dossiers (cases), their RAG index status, sources, and tools.
 */

import { useEffect, useState } from 'react';

interface Dossier {
  case_id: string;
  chunks: number | null;
  sources: number | null;
}

interface Tool {
  id: string;
  name: string;
  kind: 'local_exec' | 'url';
  target: string;
  enabled?: boolean;
}

const BASE = '/api/v1/anchorum';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export default function DossiersPanel() {
  const [dossiers, setDossiers] = useState<Dossier[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setError(null);
      const cases: string[] = await request('/cases');
      const enriched = await Promise.all(
        cases.map(async (case_id) => {
          let chunks: number | null = null;
          let sources: number | null = null;
          try {
            const stats = await request<{ indexed: boolean; chunks: number }>(`/cases/${case_id}/index`);
            chunks = stats.indexed ? stats.chunks : 0;
          } catch {
            // leave null
          }
          try {
            const src = await request<{ sources: unknown[] }>(`/cases/${case_id}/sources`);
            sources = src.sources?.length ?? 0;
          } catch {
            // leave null
          }
          return { case_id, chunks, sources };
        })
      );
      setDossiers(enriched);
      const toolData = await request<{ tools: Tool[] }>('/tools');
      setTools(toolData.tools ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--text-primary)' }}>
          Legal Dossiers
        </h2>
        <button
          onClick={load}
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
            padding: 'var(--space-2) var(--space-3)',
            borderRadius: 'var(--radius-md)',
            cursor: 'pointer',
          }}
        >
          Refresh
        </button>
      </div>

      {error && (
        <div style={{ color: 'var(--danger)', padding: 'var(--space-3)', background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)' }}>
          {error}
        </div>
      )}

      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
              <th style={{ padding: 'var(--space-2)' }}>Case ID</th>
              <th style={{ padding: 'var(--space-2)' }}>Chunks</th>
              <th style={{ padding: 'var(--space-2)' }}>Sources</th>
            </tr>
          </thead>
          <tbody>
            {dossiers.map((d) => (
              <tr key={d.case_id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: 'var(--space-2)', color: 'var(--text-primary)' }}>{d.case_id}</td>
                <td style={{ padding: 'var(--space-2)', color: 'var(--text-secondary)' }}>
                  {d.chunks === null ? '—' : d.chunks}
                </td>
                <td style={{ padding: 'var(--space-2)', color: 'var(--text-secondary)' }}>
                  {d.sources === null ? '—' : d.sources}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
        <h3 style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 'var(--space-2)' }}>
          Tools
        </h3>
        <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
          {tools.map((tool) => (
            <a
              key={tool.id}
              href={tool.kind === 'url' ? tool.target : undefined}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => {
                if (tool.kind === 'local_exec' || tool.enabled === false) {
                  e.preventDefault();
                }
              }}
              style={{
                display: 'inline-block',
                padding: 'var(--space-2) var(--space-3)',
                background: tool.enabled === false ? 'var(--bg-elevated)' : 'var(--accent-dim)',
                color: tool.enabled === false ? 'var(--text-muted)' : 'var(--accent)',
                borderRadius: 'var(--radius-md)',
                textDecoration: 'none',
                fontSize: 'var(--text-sm)',
                pointerEvents: tool.enabled === false ? 'none' : 'auto',
              }}
            >
              {tool.name} ({tool.kind})
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
