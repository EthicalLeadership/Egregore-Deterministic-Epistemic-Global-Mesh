/**
 * ANCHORUM Workspace — tab shell that renders the correct panel for each
 * sidebar item. This is the component mounted by App when the user opens
 * the ANCHORUM legal-dossier view.
 */

import { useState } from 'react';
import AnchorumLayout from './AnchorumLayout';
import FileFetchPanel from './FileFetchPanel';
import EmailIngestPanel from './EmailIngestPanel';
import DossiersPanel from './DossiersPanel';

export default function AnchorumWorkspace() {
  const [activeTab, setActiveTab] = useState('dossiers');

  return (
    <AnchorumLayout activeTab={activeTab} onTabChange={setActiveTab}>
      {activeTab === 'dossiers' && <DossiersPanel />}
      {activeTab === 'fetch' && <FileFetchPanel />}
      {activeTab === 'email' && <EmailIngestPanel />}
      {activeTab !== 'dossiers' && activeTab !== 'fetch' && activeTab !== 'email' && (
        <div style={{ color: 'var(--text-muted)', padding: '2rem' }}>
          {activeTab} panel — placeholder
        </div>
      )}
    </AnchorumLayout>
  );
}
