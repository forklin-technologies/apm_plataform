import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MotionGate } from "./MotionGate";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
}

afterEach(() => {
  setVisibility("visible");
  delete document.documentElement.dataset.motion;
});

describe("MotionGate", () => {
  it("liga data-motion so com o documento visivel", () => {
    setVisibility("visible");
    render(<MotionGate />);
    expect(document.documentElement.dataset.motion).toBe("on");
  });

  it("com o documento oculto (portal do Maestri, aba em segundo plano) NAO liga: sem animacao de entrada", () => {
    setVisibility("hidden");
    render(<MotionGate />);
    expect(document.documentElement.dataset.motion).toBeUndefined();
  });

  it("acompanha visibilitychange nos dois sentidos", () => {
    setVisibility("visible");
    render(<MotionGate />);
    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(document.documentElement.dataset.motion).toBeUndefined();
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(document.documentElement.dataset.motion).toBe("on");
  });

  it("remove o atributo ao desmontar", () => {
    setVisibility("visible");
    const { unmount } = render(<MotionGate />);
    unmount();
    expect(document.documentElement.dataset.motion).toBeUndefined();
  });
});
