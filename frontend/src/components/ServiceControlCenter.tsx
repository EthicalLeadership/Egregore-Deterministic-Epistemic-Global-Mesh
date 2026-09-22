import { useEffect, useState } from 'react';
import './ServiceControlCenter.css';

interface Service {
  name: string;
  status: string;
  enabled: string;
}

const actions = ['start', 'stop', 'restart', 'logs'];

export default function ServiceControlCenter() {
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedService, setSelectedService] = useState<string | null>(null);
  const [logs, setLogs] = useState<string>('');
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);

  const fetchServices = async () => {
    try {
      const res = await fetch('/api/v1/services/');
      if (!res.ok) throw new Error('Failed to fetch services');
      const data = await res.json();
      setServices(data);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchServices();
    const interval = setInterval(fetchServices, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleAction = async (name: string, action: string) => {
    if (action === 'logs') {
      setSelectedService(name);
      setActionInProgress(`${name}:logs`);
      try {
        const res = await fetch(`/api/v1/services/${name}/logs`);
        const data = await res.json();
        setLogs(data.logs || 'No logs');
      } catch (err: any) {
        setLogs(`Error fetching logs: ${err.message}`);
      } finally {
        setActionInProgress(null);
      }
      return;
    }
    setActionInProgress(`${name}:${action}`);
    try {
      const res = await fetch(`/api/v1/services/${name}/${action}`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        alert(`Action ${action} failed: ${err.detail || res.statusText}`);
      } else {
        await fetchServices();
      }
    } catch (err: any) {
      alert(`Network error: ${err.message}`);
    } finally {
      setActionInProgress(null);
    }
  };

  if (loading)
    return <div className="sc-loading">Chargement des services…</div>;
  if (error)
    return <div className="sc-error">Erreur: {error}</div>;

  return (
    <div className="service-control">
      <div className="sc-header">
        <h2>🔧 Contrôle des Services Egregore</h2>
        <button
          className="btn btn-refresh"
          onClick={fetchServices}
          disabled={actionInProgress !== null}
        >
          Rafraîchir
        </button>
      </div>
      <div className="service-grid">
        {services.map(service => (
          <div
            key={service.name}
            className={`service-card ${
              service.status === 'active' ? 'active' : 'inactive'
            }`}
          >
            <div className="service-name">{service.name}</div>
            <div className="service-status">
              <span
                className={`badge ${
                  service.status === 'active' ? 'badge-active' : 'badge-inactive'
                }`}
              >
                {service.status === 'active' ? '🟢 Actif' : '🔴 Inactif'}
              </span>
              <span className="badge badge-enabled">
                {service.enabled === 'enabled' ? '✓ Activé' : '✗ Désactivé'}
              </span>
            </div>
            <div className="service-actions">
              {actions.map(action => (
                <button
                  key={action}
                  className={`btn btn-${action}`}
                  onClick={() => handleAction(service.name, action)}
                  disabled={
                    actionInProgress !== null &&
                    actionInProgress !== `${service.name}:${action}`
                  }
                >
                  {actionInProgress === `${service.name}:${action}`
                    ? '⏳'
                    : action === 'start'
                      ? '▶️'
                      : action === 'stop'
                        ? '⏹️'
                        : action === 'restart'
                          ? '🔄'
                          : '📋'}
                  {' '}
                  {action.charAt(0).toUpperCase() + action.slice(1)}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      {selectedService && (
        <div className="logs-panel">
          <div className="logs-header">
            <h3>📜 Journaux: {selectedService}</h3>
            <button
              className="btn btn-close"
              onClick={() => setSelectedService(null)}
            >
              ✕ Fermer
            </button>
          </div>
          <pre className="logs-content">{logs}</pre>
        </div>
      )}
    </div>
  );
}
