"use client";

import { useEffect, useId, useRef, useState } from "react";
import { SchoolMonogram } from "@/components/portal/SchoolMonogram";
import { ChevronDownIcon } from "@/components/ui/icons";
import type { ActiveMembership, Membership } from "@/lib/api/types";
import { membershipScope, membershipTitle } from "@/lib/roles";
import { MembershipList } from "./MembershipList";

interface TenantSwitcherProps {
  memberships: Membership[];
  active: ActiveMembership;
  onSelect: (membershipId: string) => void;
  busy?: boolean;
  compact?: boolean;
}

/**
 * Seletor REAL de organizacao e escola (multi-tenant, ADR-004): lista os vinculos da sessao
 * (GET /auth/me) e troca o ativo por POST /auth/context. O cliente nunca define o tenant: so pede
 * um vinculo que ja e dele, e o servidor confere de novo.
 */
export function TenantSwitcher({ memberships, active, onSelect, busy = false, compact = false }: TenantSwitcherProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className={`flex w-full min-h-12 items-center gap-3 rounded-[14px] text-left transition-colors hover:bg-neutral-soft ${compact ? "px-1.5 py-1" : "bg-neutral-soft/70 px-3 py-2"}`}
      >
        <SchoolMonogram name={membershipTitle(active)} size={compact ? 32 : 36} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sub font-semibold leading-tight text-ink">{membershipTitle(active)}</span>
          <span className="block truncate text-foot text-ink-2">{membershipScope(active)}</span>
        </span>
        <ChevronDownIcon size={18} className={`shrink-0 text-ink-2 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          id={panelId}
          className="pop absolute left-0 top-[calc(100%+8px)] z-40 w-[min(22rem,calc(100vw-2.5rem))] origin-top rounded-[var(--r-lg)] bg-raised p-2 shadow-[var(--shadow-float)] ring-1 ring-[var(--line)]"
        >
          <MembershipList
            memberships={memberships}
            activeId={active.membershipId}
            disabled={busy}
            onSelect={(id) => {
              setOpen(false);
              triggerRef.current?.focus();
              onSelect(id);
            }}
          />
          <p className="mt-1 border-t border-line px-3 pb-2 pt-3 text-foot text-ink-2">
            As organizações e escolas vêm da sua conta, no servidor.
          </p>
        </div>
      )}
    </div>
  );
}
