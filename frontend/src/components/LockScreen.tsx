import { useState } from "react";
import Icon from "./Icon";

export default function LockScreen({ onUnlock }: { onUnlock: (password: string) => Promise<string | null> }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!password) return;
    setBusy(true);
    setError(await onUnlock(password));
    setBusy(false);
  }

  return (
    <div className="lock-wrap">
      <form className="lock-card card" onSubmit={submit}>
        <span className="brand-mark lg" aria-hidden="true">
          <Icon name="plane" size={26} />
        </span>
        <h1>Flight Scout</h1>
        <p className="muted">This app is password-protected.</p>
        <label className="field">
          <span className="field-label">App password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            aria-invalid={!!error}
            aria-describedby={error ? "lock-error" : undefined}
          />
        </label>
        {error && (
          <p className="form-error" id="lock-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary btn-lg" disabled={busy || !password}>
          <Icon name="lock" size={16} /> {busy ? "Checking…" : "Unlock"}
        </button>
      </form>
    </div>
  );
}
