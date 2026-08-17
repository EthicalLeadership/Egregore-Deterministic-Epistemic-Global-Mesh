import { useEffect, useState } from 'react';
import { Console } from './sections/Console';

export default function App() {
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  );

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReducedMotion(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);

  return (
    <Console reducedMotion={reducedMotion} onToggleMotion={() => setReducedMotion((v) => !v)} />
  );
}
