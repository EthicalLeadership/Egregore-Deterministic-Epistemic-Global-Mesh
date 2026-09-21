import { useEffect, useState } from 'react';
import { Console } from './sections/Console';
import AnchorumWorkspace from './components/anchorum/AnchorumWorkspace';
import { useDashboard } from './hooks/useDashboard';
import { useLiveEgregore } from './hooks/useLiveEgregore';

type Vertical = 'console' | 'anchorum';

export default function App() {
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  );
  const [vertical, setVertical] = useState<Vertical>('console');
  const live = useLiveEgregore(3000);
  const dashboard = useDashboard();

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReducedMotion(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);

  if (vertical === 'anchorum') {
    return <AnchorumWorkspace />;
  }

  return (
    <Console
      reducedMotion={reducedMotion}
      onToggleMotion={() => setReducedMotion((v) => !v)}
      live={live}
      onOpenAnchorum={() => setVertical('anchorum')}
      services={dashboard.services}
      loadingActions={dashboard.loadingActions}
      performServiceAction={dashboard.performServiceAction}
      toasts={dashboard.toasts}
      removeToast={dashboard.removeToast}
    />
  );
}
