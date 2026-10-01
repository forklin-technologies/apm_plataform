"use client";

import { useEffect, useState } from "react";

/**
 * Milissegundos ate `expiresAt`, atualizados a cada segundo. E SO exibicao:
 * quem diz que o Pix expirou e o status devolvido pela camada de dados.
 */
export function useCountdown(expiresAt: string, active = true): number {
  const target = Date.parse(expiresAt);
  const [remaining, setRemaining] = useState(() => Math.max(0, target - Date.now()));

  useEffect(() => {
    if (!active) return;
    const tick = () => setRemaining(Math.max(0, target - Date.now()));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [target, active]);

  return remaining;
}
