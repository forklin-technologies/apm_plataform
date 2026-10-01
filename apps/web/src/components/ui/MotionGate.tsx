"use client";

import { useEffect } from "react";

/**
 * Liga `data-motion="on"` no <html> SO enquanto o documento esta visivel.
 *
 * Por que: com o documento oculto (aba em segundo plano, portal do Maestri, Safari/WKWebView fora
 * da tela) o WebKit congela a linha do tempo das animacoes. Uma animacao de entrada parada em t=0
 * deixaria o conteudo no keyframe inicial para sempre. As animacoes de entrada em globals.css so
 * existem sob [data-motion="on"]; sem o atributo o conteudo aparece direto, no estado final.
 */
export function MotionGate() {
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => {
      if (document.visibilityState === "visible") root.dataset.motion = "on";
      else delete root.dataset.motion;
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      delete root.dataset.motion;
    };
  }, []);
  return null;
}
