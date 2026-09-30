import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import Icon from "./Icon";

export type ToastKind = "error" | "success" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

type Push = (kind: ToastKind, text: string) => void;
const ToastContext = createContext<Push>(() => {});

export function useToast(): Push {
  return useContext(ToastContext);
}

const LIFETIME: Record<ToastKind, number> = { error: 10_000, success: 7_000, info: 6_000 };

function ToastItem({ toast, onClose }: { toast: Toast; onClose: (id: number) => void }) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const t = setTimeout(() => onClose(toast.id), LIFETIME[toast.kind]);
    return () => clearTimeout(t);
  }, [paused, toast.id, toast.kind, onClose]);
  return (
    <div
      className={`toast toast-${toast.kind}`}
      role={toast.kind === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <Icon name={toast.kind === "error" ? "alert" : toast.kind === "success" ? "check" : "info"} className="toast-icon" />
      <span className="toast-text">{toast.text}</span>
      <button className="icon-btn toast-close" onClick={() => onClose(toast.id)} aria-label="Dismiss">
        <Icon name="x" size={16} />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const push = useCallback<Push>((kind, text) => {
    const id = nextId.current++;
    // identical messages replace each other instead of stacking up
    setToasts((prev) => [...prev.filter((t) => t.text !== text), { id, kind, text }].slice(-4));
  }, []);
  const close = useCallback((id: number) => setToasts((prev) => prev.filter((t) => t.id !== id)), []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onClose={close} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
