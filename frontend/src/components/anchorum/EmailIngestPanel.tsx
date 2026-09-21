/**
 * ANCHORUM Desktop — Email Ingest Panel
 * IMAP email fetcher with forensic staging.
 * Talks to the FastAPI ANCHORUM router via anchorumApi.
 */

import { useState, useRef, useEffect } from 'react';
import anchorumApi, {
  type ImapConfig,
  type ImapFolder,
  type LedgerStatus,
} from '../../lib/anchorumApi';

/* Icons */
const IconMail = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" />
  </svg>
);
const IconServer = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="2" width="20" height="8" rx="2" ry="2" /><rect x="2" y="14" width="20" height="8" rx="2" ry="2" /><line x1="6" y1="6" x2="6.01" y2="6" /><line x1="6" y1="18" x2="6.01" y2="18" />
  </svg>
);
const IconFolder = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
  </svg>
);
const IconCheck = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);
const IconAlert = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);
const IconInbox = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" /><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
  </svg>
);
const IconDownload = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
  </svg>
);
const IconSpinner = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ animation: 'spin 1s linear infinite' }}>
    <line x1="12" y1="2" x2="12" y2="6" /><line x1="12" y1="18" x2="12" y2="22" />
    <line x1="4.93" y1="4.93" x2="7.76" y2="7.76" /><line x1="16.24" y1="16.24" x2="19.07" y2="19.07" />
    <line x1="2" y1="12" x2="6" y2="12" /><line x1="18" y1="12" x2="22" y2="12" />
    <line x1="4.93" y1="19.07" x2="7.76" y2="16.24" /><line x1="16.24" y1="7.76" x2="19.07" y2="4.93" />
  </svg>
);

/* ------------------------------------------------------------------ */
/* Consent Modal */

interface ConsentModalProps {
  account: string;
  host: string;
  caseId: string;
  folders: string[];
  messageCount: number;
  loading: boolean;
  onAcceptOnce: () => void;
  onAcceptSession: () => void;
  onRefuse: () => void;
}

function ConsentModal({
  account,
  host,
  caseId,
  folders,
  messageCount,
  loading,
  onAcceptOnce,
  onAcceptSession,
  onRefuse,
}: ConsentModalProps) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000, backdropFilter: 'blur(4px)',
    }}>
      <div style={{
        background: 'var(--bg-elevated)', borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--border-strong)', width: 480,
        boxShadow: 'var(--shadow-lg)', overflow: 'hidden',
      }}>
        <div style={{
          padding: 'var(--space-5) var(--space-6)',
          borderBottom: '1px solid var(--border)',
        }}>
          <h3 style={{ fontSize: 'var(--text-lg)', fontWeight: 600, marginBottom: 'var(--space-2)' }}>
            Consent Required
          </h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)', lineHeight: 1.6 }}>
            Fetch emails from <strong style={{ color: 'var(--text-primary)' }}>{account}</strong> @
            <strong style={{ color: 'var(--text-primary)' }}> {host}</strong> for case
            <strong style={{ color: 'var(--text-primary)' }}> {caseId}</strong>.
            This action will be recorded in the consent ledger.
            {messageCount > 0 && (
              <span style={{ display: 'block', marginTop: 'var(--space-2)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>{messageCount}</strong> total message(s).
              </span>
            )}
          </p>
        </div>

        <div style={{
          maxHeight: 160, overflowY: 'auto',
          padding: 'var(--space-3) var(--space-6)',
          background: 'var(--bg-surface)',
        }}>
          {folders.map((f) => (
            <div key={f} style={{
              display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
              fontSize: 'var(--text-sm)', color: 'var(--text-secondary)',
              padding: 'var(--space-1) 0',
              borderBottom: '1px solid var(--border)',
            }}>
              <IconFolder /> {f}
            </div>
          ))}
        </div>

        <div style={{
          padding: 'var(--space-4) var(--space-6)',
          display: 'flex', gap: 'var(--space-3)', justifyContent: 'flex-end',
          borderTop: '1px solid var(--border)',
        }}>
          <button
            onClick={onRefuse}
            disabled={loading}
            style={{
              padding: 'var(--space-2) var(--space-4)',
              borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)',
              background: 'transparent', color: 'var(--text-secondary)',
              cursor: 'pointer', fontSize: 'var(--text-sm)',
            }}
          >Refuse</button>
          <button
            onClick={onAcceptOnce}
            disabled={loading}
            style={{
              padding: 'var(--space-2) var(--space-4)',
              borderRadius: 'var(--radius-md)', border: 'none',
              background: 'var(--accent)', color: 'var(--text-inverse)',
              cursor: 'pointer', fontSize: 'var(--text-sm)', fontWeight: 600,
            }}
          >{loading ? 'Fetching…' : 'Accept Once'}</button>
          <button
            onClick={onAcceptSession}
            disabled={loading}
            style={{
              padding: 'var(--space-2) var(--space-4)',
              borderRadius: 'var(--radius-md)', border: '1px solid var(--accent)',
              background: 'var(--accent-dim)', color: 'var(--accent)',
              cursor: 'pointer', fontSize: 'var(--text-sm)', fontWeight: 600,
            }}
          >{loading ? 'Fetching…' : 'Accept for Session'}</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main Panel */

interface EmailIngestPanelProps {
  caseId?: string;
  cases?: string[];
  onLedgerChange?: () => void;
}

export default function EmailIngestPanel({
  caseId = 'MOLSON-2026',
  cases = ['GDC-86849-02', 'MOLSON-2026', 'molson'],
  onLedgerChange,
}: EmailIngestPanelProps) {
  const [form, setForm] = useState({
    label: 'Primary',
    host: 'imap.gmail.com',
    port: '993',
    ssl: true,
    username: '',
    password: '',
  });
  const [activeCaseId, setActiveCaseId] = useState(caseId);
  const [folders, setFolders] = useState<ImapFolder[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<Set<string>>(new Set());
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [showConsent, setShowConsent] = useState(false);
  const [probeCount, setProbeCount] = useState(0);
  const [ledgerStatus, setLedgerStatus] = useState<LedgerStatus>({ ok: true, entries: 0, broken_at: null, message: 'intact (0 entries)' });
  const [log, setLog] = useState<{ ts: string; msg: string }[]>([]);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  const addLog = (msg: string) => {
    setLog((prev) => [...prev, { ts: new Date().toISOString(), msg }]);
    void logEndRef.current;
  };

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [log]);

  useEffect(() => {
    // Refresh ledger status on mount
    anchorumApi.ledgerStatus().then(setLedgerStatus).catch(() => undefined);
  }, []);

  const config = (): ImapConfig => ({
    host: form.host,
    port: parseInt(form.port, 10) || (form.ssl ? 993 : 143),
    username: form.username,
    password: form.password,
    useSsl: form.ssl,
  });

  const handleConnect = async () => {
    setConnecting(true);
    addLog(`Connecting to ${form.host}:${form.port}...`);
    try {
      const result = await anchorumApi.imapConnect(config());
      setFolders(result.folders);
      setConnected(true);
      addLog(`Connected. Listed ${result.folders.length} folders.`);
    } catch (err) {
      addLog(`IMAP error: ${(err as Error).message}`);
    } finally {
      setConnecting(false);
    }
  };

  const toggleFolder = (name: string) => {
    setSelectedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleFetch = async () => {
    if (selectedFolders.size === 0 || !connected) return;
    setProgress('Counting messages…');
    try {
      const probe = await anchorumApi.imapProbe(
        config(),
        Array.from(selectedFolders),
        activeCaseId
      );
      setProbeCount(probe.file_count);
      setProgress(null);
      setShowConsent(true);
    } catch (err) {
      setProgress(`Probe failed: ${(err as Error).message}`);
    }
  };

  const executeFetch = async (choice: 'once' | 'session') => {
    setShowConsent(false);
    setFetching(true);
    addLog(`Consent ${choice}. Starting fetch for ${Array.from(selectedFolders).join(', ')}...`);
    setProgress('Fetching emails…');
    try {
      const result = await anchorumApi.imapFetch(
        config(),
        Array.from(selectedFolders),
        activeCaseId,
        choice
      );
      setLedgerStatus({
        ok: true,
        entries: 0,
        broken_at: null,
        message: `intact (${result.message_count} emails staged)`,
      });
      const cancelled = result.cancelled ? ' (cancelled)' : '';
      addLog(`Fetch complete${cancelled}. ${result.message_count} emails staged.`);
      setSelectedFolders(new Set());
      setProgress(null);
    } catch (err) {
      addLog(`Fetch failed: ${(err as Error).message}`);
      setProgress(`Fetch failed: ${(err as Error).message}`);
    } finally {
      setFetching(false);
      anchorumApi.ledgerStatus().then(setLedgerStatus).catch(() => undefined);
      onLedgerChange?.();
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: 'var(--space-2) var(--space-3)',
    borderRadius: 'var(--radius-md)',
    border: '1px solid var(--border)',
    background: 'var(--bg-base)',
    color: 'var(--text-primary)',
    fontSize: 'var(--text-sm)',
    fontFamily: 'var(--font-sans)',
    outline: 'none',
    transition: 'border-color 0.15s',
  };

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: 'var(--text-xs)',
    fontWeight: 600,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    marginBottom: 'var(--space-1)',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)', height: '100%' }}>
      {/* Ledger status */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-md)',
        background: ledgerStatus.ok ? 'var(--success-dim)' : 'var(--danger-dim)',
        border: `1px solid ${ledgerStatus.ok ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
        color: ledgerStatus.ok ? 'var(--success)' : 'var(--danger)',
        fontSize: 'var(--text-sm)',
      }}>
        {ledgerStatus.ok ? <IconCheck /> : <IconAlert />}
        <span style={{ fontWeight: 600 }}>Ledger {ledgerStatus.message}</span>
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-5)', flex: 1, minHeight: 0 }}>
        {/* Config panel */}
        <div style={{
          width: 360, flexShrink: 0,
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border)',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}>
          <div style={{
            padding: 'var(--space-4) var(--space-5)',
            borderBottom: '1px solid var(--border)',
          }}>
            <h3 style={{ fontSize: 'var(--text-base)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <IconServer /> IMAP Account
            </h3>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-4) var(--space-5)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            <div>
              <label style={labelStyle}>Label</label>
              <input
                type="text" value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                style={inputStyle}
                placeholder="Primary"
              />
            </div>

            <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
              <div style={{ flex: 2 }}>
                <label style={labelStyle}>Host</label>
                <input
                  type="text" value={form.host}
                  onChange={(e) => setForm({ ...form, host: e.target.value })}
                  style={inputStyle}
                  placeholder="imap.gmail.com"
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Port</label>
                <input
                  type="text" value={form.port}
                  onChange={(e) => setForm({ ...form, port: e.target.value })}
                  style={inputStyle}
                  placeholder="993"
                />
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <input
                type="checkbox" id="ssl"
                checked={form.ssl}
                onChange={(e) => setForm({ ...form, ssl: e.target.checked })}
                style={{ width: 16, height: 16, accentColor: 'var(--accent)' }}
              />
              <label htmlFor="ssl" style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                Use SSL/TLS
              </label>
            </div>

            <div>
              <label style={labelStyle}>Username</label>
              <input
                type="text" value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                style={inputStyle}
                placeholder="user@example.com"
              />
            </div>

            <div>
              <label style={labelStyle}>Password / App Token</label>
              <input
                type="password" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                style={inputStyle}
                placeholder="••••••••"
              />
            </div>

            <div>
              <label style={labelStyle}>Case</label>
              <select
                value={activeCaseId}
                onChange={(e) => setActiveCaseId(e.target.value)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                {cases.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            <button
              onClick={handleConnect}
              disabled={connecting || connected}
              style={{
                width: '100%', padding: 'var(--space-3)',
                borderRadius: 'var(--radius-md)', border: 'none',
                background: connected ? 'var(--success-dim)' : 'var(--accent)',
                color: connected ? 'var(--success)' : 'var(--text-inverse)',
                cursor: connected ? 'default' : 'pointer',
                fontSize: 'var(--text-sm)', fontWeight: 600,
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 'var(--space-2)',
                marginTop: 'var(--space-2)',
              }}
            >
              {connecting ? <><IconSpinner /> Connecting...</> :
               connected ? <><IconCheck /> Connected</> :
               <><IconServer /> Connect & List Folders</>}
            </button>
          </div>
        </div>

        {/* Folders + Log */}
        <div style={{
          flex: 1,
          display: 'flex', flexDirection: 'column',
          gap: 'var(--space-4)', minHeight: 0,
        }}>
          {/* Folders */}
          <div style={{
            flex: 1,
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--border)',
            display: 'flex', flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <div style={{
              padding: 'var(--space-4) var(--space-5)',
              borderBottom: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <h3 style={{ fontSize: 'var(--text-base)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <IconMail /> Folders
              </h3>
              {selectedFolders.size > 0 && (
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--accent)' }}>
                  {selectedFolders.size} selected
                </span>
              )}
            </div>

            {/* Progress */}
            {progress && (
              <div style={{
                padding: 'var(--space-3) var(--space-5)',
                borderBottom: '1px solid var(--border)',
                background: 'var(--bg-elevated)',
                fontSize: 'var(--text-xs)', color: 'var(--text-secondary)',
                fontFamily: 'var(--font-mono)',
              }}>
                {progress}
              </div>
            )}

            <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-2)' }}>
              {!connected ? (
                <div style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center',
                  justifyContent: 'center', height: '100%', color: 'var(--text-muted)',
                  gap: 'var(--space-3)',
                }}>
                  <IconServer />
                  <span>Connect to an IMAP server to list folders</span>
                </div>
              ) : (
                folders.map((folder) => {
                  const isSelected = selectedFolders.has(folder.name);
                  return (
                    <div
                      key={folder.name}
                      onClick={() => toggleFolder(folder.name)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
                        padding: 'var(--space-3) var(--space-4)',
                        borderRadius: 'var(--radius-md)',
                        background: isSelected ? 'var(--accent-dim)' : 'transparent',
                        color: isSelected ? 'var(--accent)' : 'var(--text-primary)',
                        cursor: 'pointer',
                        marginBottom: 'var(--space-1)',
                        transition: 'all 0.1s',
                        border: isSelected ? '1px solid rgba(37,99,235,0.3)' : '1px solid transparent',
                      }}
                      onMouseEnter={(e) => {
                        if (!isSelected) e.currentTarget.style.background = 'var(--bg-hover)';
                      }}
                      onMouseLeave={(e) => {
                        if (!isSelected) e.currentTarget.style.background = 'transparent';
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        readOnly
                        style={{ accentColor: 'var(--accent)' }}
                      />
                      <IconInbox />
                      <span style={{ flex: 1, fontWeight: isSelected ? 600 : 400 }}>{folder.name}</span>
                      <span style={{
                        fontSize: 'var(--text-xs)', color: 'var(--text-muted)',
                        fontFamily: 'var(--font-mono)',
                      }}>
                        {folder.messages.toLocaleString()}
                      </span>
                    </div>
                  );
                })
              )}
            </div>

            {connected && (
              <div style={{
                padding: 'var(--space-3) var(--space-5)',
                borderTop: '1px solid var(--border)',
                display: 'flex', justifyContent: 'flex-end',
              }}>
                <button
                  onClick={handleFetch}
                  disabled={selectedFolders.size === 0 || fetching}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
                    padding: 'var(--space-2) var(--space-4)',
                    borderRadius: 'var(--radius-md)', border: 'none',
                    background: selectedFolders.size > 0 && !fetching ? 'var(--accent)' : 'var(--bg-hover)',
                    color: selectedFolders.size > 0 && !fetching ? 'var(--text-inverse)' : 'var(--text-muted)',
                    cursor: selectedFolders.size > 0 && !fetching ? 'pointer' : 'not-allowed',
                    fontSize: 'var(--text-sm)', fontWeight: 600,
                  }}
                >
                  <IconDownload />
                  {fetching ? 'Fetching...' : 'Fetch Selected'}
                </button>
              </div>
            )}
          </div>

          {/* Log */}
          <div style={{
            height: 180,
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--border)',
            display: 'flex', flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <div style={{
              padding: 'var(--space-3) var(--space-4)',
              borderBottom: '1px solid var(--border)',
              fontSize: 'var(--text-xs)', fontWeight: 600,
              color: 'var(--text-muted)', textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}>
              Operation Log
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-2) var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              {log.length === 0 ? (
                <span style={{ color: 'var(--text-muted)' }}>No operations yet...</span>
              ) : (
                log.map((entry, i) => (
                  <div key={i} style={{ padding: '2px 0', color: 'var(--text-secondary)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>[{entry.ts.split('T')[1]?.slice(0, 8) ?? ''}]</span>{' '}
                    {entry.msg}
                  </div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      </div>

      {/* Consent Modal */}
      {showConsent && (
        <ConsentModal
          account={form.username || form.label}
          host={form.host}
          caseId={activeCaseId}
          folders={Array.from(selectedFolders)}
          messageCount={probeCount}
          loading={fetching}
          onAcceptOnce={() => executeFetch('once')}
          onAcceptSession={() => executeFetch('session')}
          onRefuse={() => {
            setShowConsent(false);
            addLog('Fetch refused by user.');
          }}
        />
      )}

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
