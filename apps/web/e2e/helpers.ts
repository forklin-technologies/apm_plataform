import type { Page } from "@playwright/test";

export type Scenario = "normal" | "frozen" | "hidden";

/**
 * Cenarios que reproduzem a classe de defeito "conteudo invisivel porque a animacao nao rodou".
 * TODOS rodam com a CSP real do app ativa (nenhum bypassCSP): o congelamento e feito por
 * JavaScript (Web Animations API), nao por <style> injetado, que a CSP bloquearia.
 *
 *  - normal: o navegador como ele e.
 *  - frozen: toda animacao CSS (CSSAnimation) e pausada em t=0 assim que nasce, como o WebKit faz
 *    com a linha do tempo do documento oculto. Se algum keyframe inicial esconder conteudo, ele
 *    fica escondido.
 *  - hidden: o documento se declara oculto (document.hidden e visibilityState) E a linha do tempo
 *    fica congelada, exatamente o portal do Maestri. Aqui o MotionGate nao liga as animacoes de
 *    entrada; o cenario so passa se NENHUMA animacao de entrada existir (assertNoEntranceAnimations).
 *    Sem o congelamento este cenario seria vacuo para o defeito original (a animacao rodaria).
 */
export async function applyScenario(page: Page, scenario: Scenario): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      (window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI} ${e.sample ?? ""}`.trim());
    });
  });
  if (scenario === "hidden") {
    await page.addInitScript(() => {
      Object.defineProperty(Document.prototype, "visibilityState", { get: () => "hidden", configurable: true });
      Object.defineProperty(Document.prototype, "hidden", { get: () => true, configurable: true });
    });
  }
  if (scenario === "frozen" || scenario === "hidden") {
    await page.addInitScript(() => {
      const freeze = () => {
        for (const animation of document.getAnimations()) {
          if (animation instanceof CSSAnimation && animation.playState === "running") {
            animation.pause();
            animation.currentTime = 0;
          }
        }
      };
      // animationstart cobre a maioria; o laco cobre elementos novos entre dois quadros.
      document.addEventListener("animationstart", freeze, true);
      setInterval(freeze, 8);
    });
  }
}

/** Violacoes de CSP registradas na pagina desde o carregamento. */
export async function cspViolations(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? []);
}

/** Animacoes CSS de ENTRADA existentes agora (step-in, pop, stamp*); infinitas e decorativas ficam de fora. */
export async function entranceAnimations(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    document
      .getAnimations()
      .filter((a): a is CSSAnimation => a instanceof CSSAnimation && /^(rise-in|pop|stamp-)/.test(a.animationName))
      .map((a) => a.animationName),
  );
}

export async function motionAttribute(page: Page): Promise<string | undefined> {
  return page.evaluate(() => document.documentElement.dataset.motion);
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
      if (el.closest("button:disabled, [aria-disabled='true'], fieldset:disabled")) continue; // esmaecido de proposito
      if (!el.checkVisibility()) continue; // display:none, <details> fechado
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const opacity = effectiveOpacity(el);
      if (opacity < 0.9) bad.push(`${opacity.toFixed(2)} <${el.tagName.toLowerCase()}> "${text.slice(0, 48)}"`);
    }
    return bad;
  });
}

/**
 * N6: o carimbo SVG (anel e check). Cobre o que o detector de texto nao ve: tracado sumido
 * (dashoffset >= dasharray), stroke-opacity 0, opacidade efetiva baixa, escala quase zero, caixa vazia.
 * Elementos marcados data-decorative (a ondulacao) ficam de fora de proposito.
 */
export async function findInvisibleStampParts(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const stamp = document.querySelector("[data-testid='paid-stamp']");
    if (!stamp) return ["carimbo ausente"];
    const bad: string[] = [];
    for (const part of stamp.querySelectorAll<SVGGeometryElement>("[data-part]")) {
      const name = part.getAttribute("data-part");
      let opacity = 1;
      for (let node: Element | null = part; node; node = node.parentElement) opacity *= parseFloat(getComputedStyle(node).opacity);
      const style = getComputedStyle(part);
      const dash = parseFloat(style.strokeDasharray);
      const offset = parseFloat(style.strokeDashoffset);
      const matrix = new DOMMatrixReadOnly(style.transform === "none" ? undefined : style.transform);
      const scale = Math.hypot(matrix.a, matrix.b);
      const box = part.getBoundingClientRect();
      if (opacity < 0.9) bad.push(`${name}: opacidade efetiva ${opacity.toFixed(2)}`);
      if (parseFloat(style.strokeOpacity) < 0.9) bad.push(`${name}: stroke-opacity ${style.strokeOpacity}`);
      if (style.stroke === "none") bad.push(`${name}: sem stroke`);
      if (Number.isFinite(dash) && dash > 0 && Number.isFinite(offset) && Math.abs(offset) >= dash - 0.001) bad.push(`${name}: tracado escondido (dashoffset ${offset} >= dasharray ${dash})`);
      if (scale < 0.3) bad.push(`${name}: escala ${scale.toFixed(2)}`);
      if (box.width < 4 || box.height < 4) bad.push(`${name}: caixa vazia ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
    if (stamp.querySelectorAll("[data-part]").length < 2) bad.push("faltam o anel e/ou o check");
    return bad;
  });
}
