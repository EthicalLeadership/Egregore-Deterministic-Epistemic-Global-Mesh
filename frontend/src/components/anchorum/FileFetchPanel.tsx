/**
 * ANCHORUM Desktop — File Fetch Panel
 * Filesystem browser with consent-gated forensic staging.
 * Talks to the FastAPI ANCHORUM router via anchorumApi.
 */

import { useState, useCallback, useEffect } from 'react';
import anchorumApi, {
  type Partition,
  type FsEntry,
  type LedgerStatus,
} from '../../lib/anchorumApi';

/* Icons */
const IconFolder = ({ open }: { open: boolean }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: open ? 'var(--accent)' : 'var(--text-muted)' }}>
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
  </svg>
);
const IconFile = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--text-muted)' }}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
  </svg>
);
const IconChevronRight = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="9 18 15 12 9 6" />
  </svg>
);
const IconChevronDown = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);
const IconHardDrive = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="22" y1="12" x2="2" y2="12" /><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /><line x1="6" y1="16" x2="6.01" y2="16" /><line x1="10" y1="16" x2="10.01" y2="16" />
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
const IconLock = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
);
const IconDownload = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
  </svg>
);

/* ------------------------------------------------------------------ */

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'] as const;
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatPercent(used: number, total: number): number {
  if (total === 0) return 0;
  return Math.round((used / total) * 100);
}

/* ------------------------------------------------------------------ */
/* Tree Node Component */

interface TreeNodeProps {
  node: FsEntry;
  level: number;
  selected: Set<string>;
  onToggleSelect: (path: string) => void;
  onToggleExpand: (path: string) => void;
  expanded: Set<string>;
}

function TreeNode({
  node,
  level,
  selected,
  onToggleSelect,
  onToggleExpand,
  expanded,
}: TreeNodeProps) {
  const isDir = node.type === 'directory';
  const isExpanded = expanded.has(node.path);
  const isSelected = selected.has(node.path);
  const paddingLeft = level * 20 + 8;

  return (
    <div>
      <div
        onClick={() => (isDir ? onToggleExpand(node.path) : onToggleSelect(node.path))}
        style={{
          display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
          padding: 'var(--space-1) var(--space-3)',
          paddingLeft,
          cursor: 'pointer',
          borderRadius: 'var(--radius-sm)',
          background: isSelected ? 'var(--accent-dim)' : 'transparent',
          color: isSelected ? 'var(--accent)' : 'var(--text-primary)',
          transition: 'background 0.1s',
        }}
        onMouseEnter={(e) => {
          if (!isSelected) e.currentTarget.style.background = 'var(--bg-hover)';
        }}
        onMouseLeave={(e) => {
          if (!isSelected) e.currentTarget.style.background = 'transparent';
        }}
      >
        {isDir && (
          <span style={{ display: 'flex', alignItems: 'center', color: 'var(--text-muted)' }}>
            {isExpanded ? <IconChevronDown /> : <IconChevronRight />}
          </span>
        )}
        {!isDir && <span style={{ width: 12 }} />}
        <span style={{ display: 'flex', alignItems: 'center' }}>
          {isDir ? <IconFolder open={isExpanded} /> : <IconFile />}
        </span>
        <span style={{ fontSize: 'var(--text-sm)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {node.name}
        </span>
        {!isDir && (
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {formatBytes(node.size)}
          </span>
        )}
      </div>
      {isDir && isExpanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              level={level + 1}
              selected={selected}
              onToggleSelect={onToggleSelect}
              onToggleExpand={onToggleExpand}
              expanded={expanded}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Consent Modal */

interface ConsentModalProps {
  paths: string[];
  caseId: string;
  fileCount: number;
  totalBytes: number;
  loading: boolean;
  onAcceptOnce: () => void;
  onAcceptSession: () => void;
  onRefuse: () => void;
}

function ConsentModal({
  paths,
  caseId,
  fileCount,
  totalBytes,
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
            You are about to stage forensic copies of selected files for case
            <strong style={{ color: 'var(--text-primary)' }}> {caseId}</strong>.
            This action will be recorded in the consent ledger.
            {fileCount > 0 && (
              <span style={{ display: 'block', marginTop: 'var(--space-2)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>{fileCount}</strong> file(s),{' '}
                <strong style={{ color: 'var(--text-primary)' }}>{formatBytes(totalBytes)}</strong> total.
              </span>
            )}
          </p>
        </div>

        <div style={{
          maxHeight: 160, overflowY: 'auto',
          padding: 'var(--space-3) var(--space-6)',
          background: 'var(--bg-surface)',
        }}>
          {paths.map((p) => (
            <div key={p} style={{
              fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)', padding: 'var(--space-1) 0',
              borderBottom: '1px solid var(--border)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {p}
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
          >{loading ? 'Staging…' : 'Accept Once'}</button>
          <button
            onClick={onAcceptSession}
            disabled={loading}
            style={{
              padding: 'var(--space-2) var(--space-4)',
              borderRadius: 'var(--radius-md)', border: '1px solid var(--accent)',
              background: 'var(--accent-dim)', color: 'var(--accent)',
              cursor: 'pointer', fontSize: 'var(--text-sm)', fontWeight: 600,
            }}
          >{loading ? 'Staging…' : 'Accept for Session'}</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main Panel */

interface FileFetchPanelProps {
  caseId?: string;
  onLedgerChange?: () => void;
}

export default function FileFetchPanel({ caseId = 'MOLSON-2026', onLedgerChange }: FileFetchPanelProps) {
  const [partitions, setPartitions] = useState<Partition[]>([]);
  const [partitionsError, setPartitionsError] = useState<string | null>(null);
  const [selectedPartition, setSelectedPartition] = useState<string | null>(null);
  const [treeData, setTreeData] = useState<FsEntry | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showConsent, setShowConsent] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [probe, setProbe] = useState<{ count: number; bytes: number } | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [ledgerStatus, setLedgerStatus] = useState<LedgerStatus>({ ok: true, entries: 0, broken_at: null, message: 'intact (0 entries)' });

  // Load partitions + ledger status on mount
  useEffect(() => {
    anchorumApi
      .listPartitions()
      .then(setPartitions)
      .catch((err: Error) => setPartitionsError(err.message));
    anchorumApi.ledgerStatus().then(setLedgerStatus).catch(() => undefined);
  }, []);

  const loadTree = useCallback(async (mount: string) => {
    try {
      const tree = await anchorumApi.listDir(mount);
      setTreeData(tree);
      setExpanded(new Set([mount]));
      setSelectedPartition(mount);
    } catch (err) {
      setProgress(`Failed to browse ${mount}: ${(err as Error).message}`);
    }
  }, []);

  const toggleExpand = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const toggleSelect = useCallback((path: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const handleFetch = useCallback(async () => {
    if (selected.size === 0) return;
    const paths = Array.from(selected);
    setProgress('Probing selection…');
    try {
      const result = await anchorumApi.probePaths(paths, caseId);
      setProbe({ count: result.file_count, bytes: result.total_bytes });
      setShowConsent(true);
      setProgress(null);
    } catch (err) {
      setProgress(`Probe failed: ${(err as Error).message}`);
    }
  }, [selected, caseId]);

  const executeFetch = useCallback(
    async (choice: 'once' | 'session') => {
      setShowConsent(false);
      setFetching(true);
      setProgress('Staging files…');
      const paths = Array.from(selected);
      try {
        const result = await anchorumApi.fetchFiles(paths, caseId, choice);
        setLedgerStatus({
          ok: true,
          entries: 0,
          broken_at: null,
          message: result.message_count > 0
            ? `intact (${result.message_count} emails staged)`
            : `intact (${result.file_count} files staged)`,
        });
        setProgress(null);
        setSelected(new Set());
        setProbe(null);
      } catch (err) {
        setProgress(`Fetch failed: ${(err as Error).message}`);
      } finally {
        setFetching(false);
        anchorumApi.ledgerStatus().then(setLedgerStatus).catch(() => undefined);
        onLedgerChange?.();
      }
    },
    [selected, caseId, onLedgerChange]
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)', height: '100%' }}>
      {/* Ledger status banner */}
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
        {/* Partitions sidebar */}
        <div style={{
          width: 320, flexShrink: 0,
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
            <div>
              <h3 style={{ fontSize: 'var(--text-base)', fontWeight: 600 }}>Partitions</h3>
              <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginTop: 'var(--space-1)' }}>
                Select a mount point to browse
              </p>
            </div>
            <button
              onClick={() => {
                setPartitionsError(null);
                anchorumApi
                  .listPartitions()
                  .then(setPartitions)
                  .catch((err: Error) => setPartitionsError(err.message));
              }}
              style={{
                background: 'none', border: 'none', color: 'var(--text-muted)',
                cursor: 'pointer', fontSize: 'var(--text-xs)',
              }}
            >Rescan</button>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-2)' }}>
            {partitionsError && (
              <div style={{ padding: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--danger)' }}>
                {partitionsError}
              </div>
            )}
            {partitions.map((p) => {
              const used = p.total - p.free;
              const pct = formatPercent(used, p.total);
              const isSelected = selectedPartition === p.mount;

              return (
                <button
                  key={p.mount}
                  onClick={() => {
                    if (p.readable) loadTree(p.mount);
                  }}
                  style={{
                    width: '100%', textAlign: 'left',
                    padding: 'var(--space-3) var(--space-4)',
                    marginBottom: 'var(--space-2)',
                    borderRadius: 'var(--radius-md)',
                    border: 'none',
                    background: isSelected ? 'var(--accent-dim)' : 'transparent',
                    color: 'var(--text-primary)',
                    cursor: p.readable ? 'pointer' : 'not-allowed',
                    opacity: p.readable ? 1 : 0.5,
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    if (p.readable && !isSelected) e.currentTarget.style.background = 'var(--bg-hover)';
                  }}
                  onMouseLeave={(e) => {
                    if (p.readable && !isSelected) e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-2)' }}>
                    <IconHardDrive />
                    <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{p.mount}</span>
                    {!p.readable && <span style={{ marginLeft: 'auto' }}><IconLock /></span>}
                  </div>
                  <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 'var(--space-2)' }}>
                    {p.device} · {p.fstype}
                  </div>
                  {p.readable && p.total > 0 && (
                    <>
                      <div style={{
                        height: 4, borderRadius: 2,
                        background: 'var(--bg-elevated)',
                        overflow: 'hidden', marginBottom: 'var(--space-1)',
                      }}>
                        <div style={{
                          height: '100%', width: `${pct}%`,
                          background: pct > 90 ? 'var(--danger)' : pct > 75 ? 'var(--warning)' : 'var(--accent)',
                          borderRadius: 2, transition: 'width 0.3s',
                        }} />
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                        <span>{formatBytes(used)} used</span>
                        <span>{formatBytes(p.free)} free</span>
                      </div>
                    </>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* File tree */}
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
            <div>
              <h3 style={{ fontSize: 'var(--text-base)', fontWeight: 600 }}>
                {selectedPartition || 'Select a partition'}
              </h3>
              {selected.size > 0 && (
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--accent)', marginTop: 'var(--space-1)' }}>
                  {selected.size} item{selected.size !== 1 ? 's' : ''} selected
                </p>
              )}
            </div>
            <button
              onClick={handleFetch}
              disabled={selected.size === 0 || fetching}
              style={{
                display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
                padding: 'var(--space-2) var(--space-4)',
                borderRadius: 'var(--radius-md)', border: 'none',
                background: selected.size > 0 && !fetching ? 'var(--accent)' : 'var(--bg-hover)',
                color: selected.size > 0 && !fetching ? 'var(--text-inverse)' : 'var(--text-muted)',
                cursor: selected.size > 0 && !fetching ? 'pointer' : 'not-allowed',
                fontSize: 'var(--text-sm)', fontWeight: 600,
                transition: 'all 0.15s',
              }}
            >
              <IconDownload />
              {fetching ? 'Staging...' : 'Stage Selected'}
            </button>
          </div>

          {/* Status line */}
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

          {/* Tree */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-2)' }}>
            {treeData ? (
              <TreeNode
                node={treeData}
                level={0}
                selected={selected}
                onToggleSelect={toggleSelect}
                onToggleExpand={toggleExpand}
                expanded={expanded}
              />
            ) : (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: '100%', color: 'var(--text-muted)',
                gap: 'var(--space-3)',
              }}>
                <IconHardDrive />
                <span>Select a partition to browse files</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Consent Modal */}
      {showConsent && (
        <ConsentModal
          paths={Array.from(selected)}
          caseId={caseId}
          fileCount={probe?.count ?? 0}
          totalBytes={probe?.bytes ?? 0}
          loading={fetching}
          onAcceptOnce={() => executeFetch('once')}
          onAcceptSession={() => executeFetch('session')}
          onRefuse={() => {
            setShowConsent(false);
            setProgress(null);
          }}
        />
      )}
    </div>
  );
}
