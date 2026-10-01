import type { Page } from "@playwright/test";

export type Scenario = "normal" | "frozen" | "hidden";

/**
 * Cenarios que reproduzem a classe de defeito "conteudo invisivel porque a animacao nao rodou":
 *  - normal: o navegador como ele e;
 *  - frozen: TODA animacao CSS fica pausada em t=0 (linha do tempo congelada, como o WebKit faz
 *    com o documento oculto). Se algum keyframe inicial esconder conteudo, ele fica escondido;
 *  - hidden: o documento se declara oculto (document.hidden e visibilityState), como no portal do
 *    Maestri. O MotionGate nao liga as animacoes de entrada.
 * Requer `bypassCSP` no contexto apenas para injetar o <style> do cenario frozen.
 */
export async function applyScenario(page: Page, scenario: Scenario): Promise<void> {
  if (scenario === "hidden") {
    await page.addInitScript(() => {
      Object.defineProperty(Document.prototype, "visibilityState", { get: () => "hidden", configurable: true });
      Object.defineProperty(Document.prototype, "hidden", { get: () => true, configurable: true });
    });
  }
  if (scenario === "frozen") {
    await page.addInitScript(() => {
      const css = "*,*::before,*::after{animation-play-state:paused !important;animation-delay:0s !important}";
      const inject = () => {
        const style = document.createElement("style");
        style.textContent = css;
        document.documentElement.appendChild(style);
      };
      if (document.documentElement) inject();
      else document.addEventListener("DOMContentLoaded", inject, { once: true });
    });
  }
}

/** Texto visivel cuja opacidade efetiva (produto da cadeia de ancestrais) e menor que 0,9. */
export async function findInvisibleText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = [];
    const effectiveOpacity = (el: Element): number => {
      let opacity = 1;
      for (let node: Element | null = el; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.visibility === "hidden") return 0;
        opacity *= parseFloat(style.opacity);
      }
      return opacity;
    };
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const text = (node.nodeValue ?? "").trim();
      const el = node.parentElement;
      if (!text || !el) continue;
      if (el.closest("script,style,noscript,[hidden]")) continue;
      if (el.closest("[class*='sr-only']")) continue; // texto so para leitor de tela
      if (el.closest("button:disabled, [aria-disabled='true'], fieldset:disabled")) continue; // desativado: esmaecido de proposito
      if (!el.checkVisibility()) continue; // display:none, <details> fechado
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const opacity = effectiveOpacity(el);
      if (opacity < 0.9) bad.push(`${opacity.toFixed(2)} <${el.tagName.toLowerCase()}> "${text.slice(0, 48)}"`);
    }
    return bad;
  });
}
