/**
 * ANCHORUM Desktop — Main Layout
 * Sidebar + header + content area with tab navigation.
 * Ported from AnchorumLayout(1).jsx into the Vite/React shell.
 */

import { useState, type ReactNode } from 'react';
import './anchorum-theme.css';

/* Icons as inline SVG components — no external deps */
const IconCases = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 8v13H3V8" /><path d="M1 3h22v5H1z" /><path d="M10 12h4" />
  </svg>
);
const IconAgent = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" /><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83" />
  </svg>
);
const IconBatch = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
  </svg>
);
const IconJobs = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
  </svg>
);
const IconSystem = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2a10 10 0 1 0 10 10H12V2z" /><path d="M12 2a10 10 0 0 1 10 10" /><path d="M12 12L2.5 8.5" />
  </svg>
);
const IconFactory = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 22h20" /><path d="M6 22V10l6-4v16" /><path d="M18 22V8l-6 4v10" />
  </svg>
);
const IconFetch = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
  </svg>
);
const IconEmail = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" />
  </svg>
);
const IconChevron = ({ dir = 'right' }: { dir?: 'right' | 'down' }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: dir === 'down' ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s' }}>
    <polyline points="9 18 15 12 9 6" />
  </svg>
);
const IconShield = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
);
const IconShieldAlert = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);
const IconSearch = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
);
const IconRefresh = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
  </svg>
);

/* ------------------------------------------------------------------ */

const IconDossiers = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const SIDEBAR_ITEMS = [
  { id: 'dossiers', label: 'Dossiers', icon: <IconDossiers /> },
  { id: 'cases', label: 'Cases', icon: <IconCases /> },
  { id: 'agent', label: 'AI Agent', icon: <IconAgent /> },
  { id: 'batch', label: 'Batch / Fusion', icon: <IconBatch /> },
  { id: 'jobs', label: 'Jobs', icon: <IconJobs /> },
  { id: 'system', label: 'System', icon: <IconSystem /> },
  { id: 'factory', label: 'Factory', icon: <IconFactory /> },
  { id: 'fetch', label: 'Fetch', icon: <IconFetch /> },
  { id: 'email', label: 'Email', icon: <IconEmail /> },
] as const;

export type AnchorumTab = (typeof SIDEBAR_ITEMS)[number]['id'];

interface AnchorumLayoutProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  children: ReactNode;
  ledgerStatus?: { ok: boolean; message: string } | null;
}

export default function AnchorumLayout({
  activeTab,
  onTabChange,
  children,
  ledgerStatus,
}: AnchorumLayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const filtered = SIDEBAR_ITEMS.filter((i) =>
    i.label.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="anchorum-root" style={{ display: 'flex', height: '100vh', background: 'var(--bg-base)' }}>
      {/* Sidebar */}
      <aside
        style={{
          width: collapsed ? 'var(--sidebar-collapsed)' : 'var(--sidebar-width)',
          background: 'var(--bg-surface)',
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          transition: 'width 0.25s ease',
          flexShrink: 0,
          overflow: 'hidden',
        }}
      >
        {/* Logo */}
        <div style={{
          height: 'var(--header-height)',
          display: 'flex',
          alignItems: 'center',
          padding: collapsed ? '0 18px' : '0 var(--space-5)',
          borderBottom: '1px solid var(--border)',
          gap: 'var(--space-3)',
        }}>
          <div style={{
            width: 28, height: 28, borderRadius: 'var(--radius-sm)',
            background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--text-inverse)', fontWeight: 700, fontSize: 14, flexShrink: 0,
          }}>A</div>
          {!collapsed && (
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)', letterSpacing: '0.02em' }}>ANCHORUM</span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginTop: -2 }}>Legal Dossier AI</span>
            </div>
          )}
        </div>

        {/* Search */}
        {!collapsed && (
          <div style={{ padding: 'var(--space-3) var(--space-4)' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
              background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)',
              padding: 'var(--space-2) var(--space-3)',
              border: '1px solid var(--border)',
            }}>
              <IconSearch />
              <input
                type="text"
                placeholder="Find tool..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  background: 'transparent', border: 'none', outline: 'none',
                  color: 'var(--text-primary)', fontSize: 'var(--text-sm)',
                  width: '100%',
                }}
              />
            </div>
          </div>
        )}

        {/* Nav */}
        <nav style={{ flex: 1, padding: 'var(--space-2)', overflowY: 'auto' }}>
          {filtered.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onTabChange(item.id)}
                title={collapsed ? item.label : undefined}
                style={{
                  width: '100%',
                  display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
                  padding: collapsed ? 'var(--space-3) 0' : 'var(--space-3) var(--space-4)',
                  justifyContent: collapsed ? 'center' : 'flex-start',
                  marginBottom: 'var(--space-1)',
                  borderRadius: 'var(--radius-md)',
                  border: 'none',
                  background: isActive ? 'var(--accent-dim)' : 'transparent',
                  color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  fontSize: 'var(--text-sm)',
                  fontWeight: isActive ? 600 : 400,
                  transition: 'all 0.15s',
                  position: 'relative',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.background = 'var(--bg-hover)';
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.background = 'transparent';
                }}
              >
                {isActive && (
                  <div style={{
                    position: 'absolute', left: 0, top: '20%', height: '60%',
                    width: 3, borderRadius: 2, background: 'var(--accent)',
                  }} />
                )}
                <span style={{ display: 'flex', alignItems: 'center' }}>{item.icon}</span>
                {!collapsed && <span>{item.label}</span>}
              </button>
            );
          })}
        </nav>

        {/* Ledger status footer */}
        <div style={{
          padding: 'var(--space-3) var(--space-4)',
          borderTop: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
          fontSize: 'var(--text-xs)',
          color: ledgerStatus?.ok ? 'var(--success)' : ledgerStatus ? 'var(--danger)' : 'var(--text-muted)',
        }}>
          {ledgerStatus?.ok ? <IconShield /> : ledgerStatus ? <IconShieldAlert /> : <IconShield />}
          {!collapsed && (
            <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {ledgerStatus?.message || 'Ledger ready'}
            </span>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          style={{
            width: '100%', padding: 'var(--space-2)',
            background: 'transparent', border: 'none',
            borderTop: '1px solid var(--border)',
            color: 'var(--text-muted)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div style={{ transform: collapsed ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>
            <IconChevron />
          </div>
        </button>
      </aside>

      {/* Main content */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Header */}
        <header style={{
          height: 'var(--header-height)',
          background: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0 var(--space-6)',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <h1 style={{
              fontSize: 'var(--text-lg)', fontWeight: 600,
              color: 'var(--text-primary)', letterSpacing: '-0.01em',
            }}>
              {SIDEBAR_ITEMS.find((i) => i.id === activeTab)?.label || 'ANCHORUM'}
            </h1>
            <span style={{
              background: 'var(--accent-dim)', color: 'var(--accent)',
              padding: '2px 8px', borderRadius: 'var(--radius-sm)',
              fontSize: 'var(--text-xs)', fontWeight: 600,
            }}>v0.6.0</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)' }}>
            <button style={{
              display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
              background: 'var(--bg-elevated)', border: '1px solid var(--border)',
              color: 'var(--text-secondary)', padding: 'var(--space-2) var(--space-3)',
              borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)',
            }}>
              <IconRefresh /> Refresh
            </button>
            <div style={{
              width: 32, height: 32, borderRadius: '50%',
              background: 'var(--accent)', display: 'flex',
              alignItems: 'center', justifyContent: 'center',
              fontWeight: 700, fontSize: 13, color: 'var(--text-inverse)',
            }}>S</div>
          </div>
        </header>

        {/* Content area */}
        <div style={{
          flex: 1, overflow: 'auto', padding: 'var(--space-6)',
        }}>
          {children}
        </div>
      </main>
    </div>
  );
}
