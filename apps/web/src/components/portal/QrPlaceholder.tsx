import { hashString } from "@/lib/hash";

const SIZE = 29;

function inFinder(x: number, y: number): "dark" | "light" | null {
  const corners: Array<[number, number]> = [
    [0, 0],
    [SIZE - 7, 0],
    [0, SIZE - 7],
  ];
  for (const [cx, cy] of corners) {
    const dx = x - cx;
    const dy = y - cy;
    if (dx >= -1 && dx <= 7 && dy >= -1 && dy <= 7) {
      if (dx < 0 || dy < 0 || dx > 6 || dy > 6) return "light";
      const ring = dx === 0 || dx === 6 || dy === 0 || dy === 6;
      const core = dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4;
      return ring || core ? "dark" : "light";
    }
  }
  return null;
}

/**
 * QR de EXEMPLO. Imitacao visual gerada a partir do texto (sem estrutura valida de QR) e com um
 * selo por cima: nenhum app de banco le isto, de proposito. O QR real vem do payload do backend.
 */
export function QrPlaceholder({ payload }: { payload: string }) {
  let state = hashString(payload) || 1;
  const next = () => {
    state ^= state << 13;
    state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return state;
  };
  const cells: string[] = [];
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const finder = inFinder(x, y);
      const dark = finder ? finder === "dark" : next() % 100 < 47;
      if (dark) cells.push(`M${x} ${y}h1v1h-1z`);
    }
  }
  return (
    <div className="relative mx-auto aspect-square w-full max-w-[17rem] rounded-[var(--r-lg)] bg-white p-4 shadow-[0_0_0_1px_var(--line)]">
      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className="size-full"
        role="img"
        aria-label="QR Code de exemplo. Não é um Pix real e nenhum aplicativo de banco consegue ler."
        shapeRendering="crispEdges"
      >
        <path d={cells.join("")} fill="#14171c" />
      </svg>
      <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 -rotate-6 rounded-full bg-warn-soft px-4 py-2 text-sub font-bold text-warn shadow-[0_0_0_4px_#fff]">
        Exemplo, não pague
      </span>
    </div>
  );
}
