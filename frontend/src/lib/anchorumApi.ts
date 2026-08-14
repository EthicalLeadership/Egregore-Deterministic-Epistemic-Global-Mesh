/**
 * ANCHORUM desktop API client.
 *
 * Talks to the FastAPI ANCHORUM router through the Vite dev proxy / Node
 * gateway. Real endpoints (consent-gated staging, consent ledger, IMAP):
 *   GET    /api/v1/anchorum/fs/partitions
 *   GET    /api/v1/anchorum/fs/list?path=...
 *   POST   /api/v1/anchorum/fs/probe        { paths, case_id }
 *   POST   /api/v1/anchorum/fs/fetch        { paths, case_id, consent }
 *   GET    /api/v1/anchorum/imap/ledger
 *   POST   /api/v1/anchorum/imap/connect    IMAP config
 *   POST   /api/v1/anchorum/imap/probe      { config, folders, case_id }
 *   POST   /api/v1/anchorum/imap/fetch      { config, folders, case_id, consent }
 */

export interface Partition {
  mount: string;
  device: string;
  fstype: string;
  total: number;
  free: number;
  readable: boolean;
}

export interface FsEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number;
  children?: FsEntry[];
}

export interface ProbeResult {
  file_count: number;
  total_bytes: number;
  errors: string[];
}

export interface LedgerStatus {
  ok: boolean;
  entries: number;
  broken_at: number | null;
  message: string;
}

export interface ImapFolder {
  name: string;
  flags: string;
  messages: number;
}

export interface ImapConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  useSsl: boolean;
}

export interface ConsentChoice {
  choice: 'once' | 'session' | 'refuse';
}

export interface FetchResult {
  staging_dir: string;
  file_count: number;
  message_count: number;
  total_bytes: number;
  errors: string[];
  cancelled?: boolean;
}

const BASE = '/api/v1/anchorum';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail =
      body?.detail ??
      (typeof body?.message === 'string' ? body.message : `HTTP ${response.status}`);
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function encodePath(path: string): string {
  return encodeURIComponent(path);
}

const anchorumApi = {
  // ---- Filesystem ----
  listPartitions(): Promise<Partition[]> {
    return request<Partition[]>('/fs/partitions');
  },

  listDir(path: string): Promise<FsEntry> {
    return request<FsEntry>(`/fs/list?path=${encodePath(path)}`);
  },

  probePaths(paths: string[], caseId: string): Promise<ProbeResult> {
    return request<ProbeResult>('/fs/probe', {
      method: 'POST',
      body: JSON.stringify({ paths, case_id: caseId }),
    });
  },

  fetchFiles(paths: string[], caseId: string, choice: ConsentChoice['choice']): Promise<FetchResult> {
    return request<FetchResult>('/fs/fetch', {
      method: 'POST',
      body: JSON.stringify({ paths, case_id: caseId, consent: choice }),
    });
  },

  // ---- Email / IMAP ----
  ledgerStatus(): Promise<LedgerStatus> {
    return request<LedgerStatus>('/imap/ledger');
  },

  imapConnect(config: ImapConfig): Promise<{ folders: ImapFolder[] }> {
    return request<{ folders: ImapFolder[] }>('/imap/connect', {
      method: 'POST',
      body: JSON.stringify({ config }),
    });
  },

  imapProbe(config: ImapConfig, folders: string[], caseId: string): Promise<ProbeResult> {
    return request<ProbeResult>('/imap/probe', {
      method: 'POST',
      body: JSON.stringify({ config, folders, case_id: caseId }),
    });
  },

  imapFetch(
    config: ImapConfig,
    folders: string[],
    caseId: string,
    choice: ConsentChoice['choice']
  ): Promise<FetchResult> {
    return request<FetchResult>('/imap/fetch', {
      method: 'POST',
      body: JSON.stringify({ config, folders, case_id: caseId, consent: choice }),
    });
  },
};

export default anchorumApi;
