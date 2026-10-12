export function Logo({ small = false }: { small?: boolean }) {
  return (
    <span className={`brand-mark ${small ? "small" : ""}`} aria-hidden="true">
      <img
        src="/lenny-mark.svg"
        alt=""
        width={small ? 24 : 36}
        height={small ? 24 : 36}
      />
    </span>
  );
}

export function StartupScreen() {
  return (
    <div className="startup-screen" role="status" aria-live="polite">
      <div className="startup-brand">
        <Logo />
        <strong>
          lenny<span>.</span>
        </strong>
      </div>
      <p>Opening your workspace…</p>
    </div>
  );
}
