import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { SCHOOL } from "@/test-utils/public-fixtures";
import { AmountStep, CUSTOM_CHOICE } from "./AmountStep";

const school = SCHOOL;

function Harness({ initial = 0 }: { initial?: number }) {
  const [cents, setCents] = useState(initial);
  return (
    <AmountStep
      school={school}
      choice={CUSTOM_CHOICE}
      customCents={cents}
      error={null}
      headingRef={() => {}}
      onChoice={() => {}}
      onCustomChange={setCents}
      onContinue={() => {}}
    />
  );
}

const field = () => screen.getByLabelText("Valor da contribuição") as HTMLInputElement;
const paste = (text: string) => fireEvent.paste(field(), { clipboardData: { getData: () => text } });

describe("N10: colar no campo de valor interpreta REAIS", () => {
  it.each([
    ["1.000", "1.000,00"],
    ["1,50", "1,50"],
    ["1500", "1.500,00"],
    ["R$ 20,00", "20,00"],
    ["  1.234,56  ", "1.234,56"],
    ["12", "12,00"],
    ["0,05", "0,05"],
  ])("colar %j mostra %s", (pasted, shown) => {
    render(<Harness />);
    paste(pasted);
    expect(field().value).toBe(shown);
    expect(screen.queryByText(/Não entendemos o que foi colado/)).not.toBeInTheDocument();
  });

  it("colar substitui o valor que ja estava no campo (nao concatena)", () => {
    render(<Harness initial={1550} />);
    expect(field().value).toBe("15,50");
    paste("1.000");
    expect(field().value).toBe("1.000,00");
  });

  it.each(["abc", "R$", "", "1.5", "1,234", "12.50", "-5", "0", "0,00", "99999999999999", "1.000,00 reais"])(
    "texto que nao e valor (%j) e ignorado, com aviso curto",
    (pasted) => {
      render(<Harness initial={2500} />);
      paste(pasted);
      expect(field().value).toBe("25,00"); // nao mudou
      expect(screen.getByText(/Não entendemos o que foi colado/)).toBeInTheDocument();
    },
  );

  it("o aviso some assim que o usuario digita um valor", async () => {
    render(<Harness />);
    paste("abc");
    expect(screen.getByText(/Não entendemos/)).toBeInTheDocument();
    await userEvent.setup().type(field(), "7");
    expect(screen.queryByText(/Não entendemos/)).not.toBeInTheDocument();
    expect(field().value).toBe("0,07");
  });

  it("soltar texto (drop) tambem e lido como reais", () => {
    render(<Harness />);
    fireEvent.drop(field(), { dataTransfer: { getData: () => "2.000" } });
    expect(field().value).toBe("2.000,00");
  });

  it("digitar continua sendo estilo caixa eletronico (digitos viram centavos), como a mascara mostra", async () => {
    render(<Harness />);
    await userEvent.setup().type(field(), "1550");
    expect(field().value).toBe("15,50");
  });
});

describe("opcoes de valor vindas da escola", () => {
  const renderStep = (overrides: Partial<typeof SCHOOL> = {}, choice: string | null = null) =>
    render(
      <AmountStep
        school={{ ...SCHOOL, ...overrides }}
        choice={choice}
        customCents={0}
        error={null}
        headingRef={() => {}}
        onChoice={() => {}}
        onCustomChange={() => {}}
        onContinue={() => {}}
      />,
    );

  it("mostra os valores sugeridos e 'Outro valor' com os limites da API", () => {
    renderStep();
    expect(screen.getAllByRole("radio")).toHaveLength(4);
    expect(screen.getByText(/R\$\s20,00/)).toBeInTheDocument();
    expect(screen.getByText(/R\$\s40,00/)).toBeInTheDocument();
    expect(screen.getByText(/Entre R\$\s10,00 e R\$\s5\.000,00/)).toBeInTheDocument();
  });

  it("allow_custom_amount=false: so os valores sugeridos, sem 'Outro valor'", () => {
    renderStep({ allowCustomAmount: false });
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.queryByText("Outro valor")).not.toBeInTheDocument();
  });
});
