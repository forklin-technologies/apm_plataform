import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import ErrorPage from "./error";
import GlobalError from "./global-error";

const secret = new Error("senha=1234 em /var/app/segredo.ts") as Error & { digest?: string };
secret.digest = "a1b2c3";

describe("N2: error.tsx", () => {
  it("fala portugues, tem a identidade do produto e o marcador de prototipo", () => {
    render(<ErrorPage error={secret} reset={vi.fn()} />);
    expect(screen.getByRole("heading", { level: 1, name: "Algo deu errado por aqui" })).toBeInTheDocument();
    expect(screen.getByText("APM Digital")).toBeInTheDocument();
    expect(screen.getByText("Protótipo · dados de exemplo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir para o início" })).toHaveAttribute("href", "/");
  });

  it("nunca vaza a mensagem do erro; mostra so o codigo (digest)", () => {
    render(<ErrorPage error={secret} reset={vi.fn()} />);
    expect(document.body.textContent).not.toMatch(/senha|segredo|1234|\/var\/app/);
    expect(screen.getByText(/Código do erro: a1b2c3/)).toBeInTheDocument();
  });

  it("'Tentar de novo' chama o reset", async () => {
    const reset = vi.fn();
    render(<ErrorPage error={secret} reset={reset} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});

describe("N2: global-error.tsx", () => {
  const html = renderToStaticMarkup(<GlobalError error={secret} reset={() => {}} />);

  it("traz <html lang=\"pt-BR\"> e <body> proprios", () => {
    expect(html.startsWith('<html lang="pt-BR">')).toBe(true);
    expect(html).toContain("<body>");
    expect(html).toContain("<title>Algo deu errado · APM Digital</title>");
  });

  it("mesmo texto em portugues e identidade, sem vazar a mensagem", () => {
    expect(html).toContain("Algo deu errado por aqui");
    expect(html).toContain("APM Digital");
    expect(html).toContain("Protótipo · dados de exemplo");
    expect(html).not.toMatch(/senha|segredo|1234/);
    expect(html).toContain("Tentar de novo");
  });
});
