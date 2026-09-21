import { useEffect, useState } from 'react';
import { CheckCircle, AlertCircle, Info, X } from 'lucide-react';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

interface Toast {
  id: string;
  message: string;
  type: ToastType;
}

interface Props {
  toasts: Toast[];
  onRemove: (id: string) => void;
}

const config: Record<ToastType, { icon: typeof CheckCircle; bg: string; border: string; text: string }> = {
  success: { icon: CheckCircle, bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', text: 'text-emerald-400' },
  error:   { icon: AlertCircle, bg: 'bg-red-500/10',     border: 'border-red-500/20',     text: 'text-red-400' },
  warning: { icon: AlertCircle, bg: 'bg-amber-500/10',   border: 'border-amber-500/20',   text: 'text-amber-400' },
  info:    { icon: Info,        bg: 'bg-blue-500/10',    border: 'border-blue-500/20',    text: 'text-blue-400' },
};

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: (id: string) => void }) {
  const [visible, setVisible] = useState(false);
  const c = config[toast.type];
  const Icon = c.icon;

  useEffect(() => {
    requestAnimationFrame(() => setVisible(true));
    const t1 = setTimeout(() => { setVisible(false); }, 3700);
    const t2 = setTimeout(() => { onRemove(toast.id); }, 4000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [toast.id, onRemove]);

  return (
    <div className={`flex items-center gap-2.5 rounded-lg border px-3.5 py-2.5 min-w-[280px] max-w-sm transition-all duration-300 ${c.bg} ${c.border}`}
      style={{ transform: visible ? 'translateX(0)' : 'translateX(120%)', opacity: visible ? 1 : 0 }}>
      <Icon className={`h-4 w-4 shrink-0 ${c.text}`} />
      <span className={`flex-1 text-xs font-medium ${c.text}`}>{toast.message}</span>
      <button onClick={() => onRemove(toast.id)} className={`shrink-0 opacity-50 hover:opacity-100 ${c.text}`}>
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

export default function ToastNotification({ toasts, onRemove }: Props) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-16 right-4 z-[200] flex flex-col gap-2">
      {toasts.map(t => <ToastItem key={t.id} toast={t} onRemove={onRemove} />)}
    </div>
  );
}
