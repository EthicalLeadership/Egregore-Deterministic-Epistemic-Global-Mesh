import { useState } from 'react';
import Header from './components/Header';
import ServiceCard from './components/ServiceCard';
import MetricsGrid from './components/MetricsGrid';
import LogViewer from './components/LogViewer';
import SettingsPanel from './components/SettingsPanel';
import HealthBar from './components/HealthBar';
import ToastNotification from './components/ToastNotification';
import { useDashboard } from './hooks/useDashboard';
import { Loader2 } from 'lucide-react';

function App() {
  const {
    services,
    metrics,
    health,
    loading,
    loadingActions,
    toasts,
    isConnected,
    performServiceAction,
    removeToast,
  } = useDashboard();

  const [logsOpen, setLogsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  if (loading) {
    return (
      <div className="fixed inset-0 bg-[#0f1117] flex items-center justify-center gap-3">
        <Loader2 className="h-5 w-5 text-[#2563eb] animate-spin" />
        <span className="text-sm text-slate-400">Loading...</span>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-[#e2e4e9]">
      {/* Header */}
      <Header
        onOpenLogs={() => setLogsOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
        isConnected={isConnected}
      />

      {/* Main Content */}
      <main className="mx-auto max-w-[1440px] px-6 pt-20 pb-12">
        {/* Title + Status */}
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-xl font-semibold text-white">Dashboard</h1>
            <p className="mt-0.5 text-sm text-slate-500">
              Monitor and manage your Egregore services
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="status-dot status-running" />
            Auto-refreshing every 3s
          </div>
        </div>

        {/* Service Cards */}
        <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {services.map((service) => (
            <ServiceCard
              key={service.name}
              service={service}
              loading={loadingActions[service.name] || false}
              onAction={performServiceAction}
            />
          ))}
        </div>

        {/* Metrics Grid */}
        {metrics && <MetricsGrid metrics={metrics} />}

        {/* Health */}
        {health && <HealthBar health={health} />}
      </main>

      {/* Overlays */}
      <LogViewer isOpen={logsOpen} onClose={() => setLogsOpen(false)} />
      <SettingsPanel isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <ToastNotification toasts={toasts} onRemove={removeToast} />
    </div>
  );
}

export default App;
