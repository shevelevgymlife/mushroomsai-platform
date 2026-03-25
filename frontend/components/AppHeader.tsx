"use client";

/**
 * Эталонный React/Next.js + Tailwind компонент (в продакшене используется
 * web/templates/components/app_mobile_header.html + app-mobile-shell.css).
 *
 * Пример layout (app/layout.tsx):
 *   import { AppHeader } from "@/frontend/components/AppHeader"; // при Next.js добавьте "next/link" для Link
 *   export default function RootLayout({ children }) {
 *     return (
 *       <html><body className="bg-black text-zinc-100">
 *         <AppHeader effectiveUserId={123} />
 *         <main className="pt-[calc(56px+env(safe-area-inset-top))]">{children}</main>
 *       </body></html>
 *     );
 *   }
 */

type AppHeaderProps = {
  effectiveUserId: number;
  profileHref?: string;
  securityHref?: string;
};

const btn =
  "relative flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] border border-cyan-400/40 bg-transparent text-cyan-300 shadow-[0_0_16px_rgba(34,211,238,0.12),inset_0_0_20px_rgba(34,211,238,0.04)] transition hover:border-cyan-300/75 hover:text-cyan-200 hover:shadow-[0_0_22px_rgba(34,211,238,0.28)] active:scale-[0.96]";

export function AppHeader({
  effectiveUserId,
  profileHref = `/community/profile/${effectiveUserId}`,
  securityHref = "/account/link",
}: AppHeaderProps) {
  return (
    <div
      className="pointer-events-none fixed left-0 right-0 top-0 z-[1000] flex justify-center px-[max(0px,env(safe-area-inset-left))] pr-[max(0px,env(safe-area-inset-right))] pt-[env(safe-area-inset-top)]"
      role="presentation"
    >
      <header
        className="pointer-events-auto flex min-h-[56px] w-full max-w-[430px] items-center justify-between gap-2 border-b border-cyan-400/20 bg-black px-3 py-2 shadow-[0_0_28px_rgba(34,211,238,0.06),0_8px_32px_rgba(0,0,0,0.65),inset_0_-1px_0_rgba(34,211,238,0.12)] sm:rounded-b-2xl sm:border-x sm:border-cyan-400/10"
        aria-label="NEUROFUNGI AI"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <button type="button" className={btn} aria-label="Открыть меню">
            <MenuIcon />
          </button>
          <a
            href={profileHref}
            className="min-w-0 truncate bg-gradient-to-r from-lime-300 via-fuchsia-300 to-cyan-400 bg-clip-text font-sans text-[clamp(0.78rem,2.8vw,1.05rem)] font-extrabold uppercase leading-tight tracking-wide text-transparent drop-shadow-[0_0_12px_rgba(34,211,238,0.15)]"
          >
            NEUROFUNGI AI
          </a>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button type="button" className={`${btn} text-sky-100`} aria-label="Уведомления">
            <BellIcon />
          </button>
          <a href={securityHref} className={btn} aria-label="Безопасность и аккаунт" title="Безопасность и аккаунт">
            <ShieldIcon />
          </a>
          <a href={profileHref} className={btn} aria-label="Профиль" title="Профиль">
            <UserIcon />
          </a>
        </div>
      </header>
    </div>
  );
}

function MenuIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" aria-hidden>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function BellIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" aria-hidden>
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 7h18s-3 0-3-7" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function UserIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="8" r="4" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </svg>
  );
}
