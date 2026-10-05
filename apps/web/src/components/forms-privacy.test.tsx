import { fireEvent, render } from "@testing-library/react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { SCHOOLS } from "@/mocks/fixtures";
import { LoginForm } from "./login/LoginForm";
import { AmountStep } from "./portal/AmountStep";
import { IdentificationStep } from "./portal/IdentificationStep";

/**
 * N8 (privacidade, dado de crianca): nenhum <form> pode cair num GET com dados na URL (por exemplo,
 * Enter antes da hidratacao do React): todos sao method="post" e fazem preventDefault no submit.
 */
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }) }));

const school = SCHOOLS[0]!;
const noop = () => {};

describe("formularios nunca vazam dados na URL", () => {
  it("passo do valor: method=post e submit com preventDefault", () => {
    const { container } = render(
      <AmountStep school={school} choice="custom" customCents={0} error={null} headingRef={noop} onChoice={noop} onCustomChange={noop} onContinue={noop} />,
    );
    const form = container.querySelector("form")!;
    expect(form.method).toBe("post");
    expect(fireEvent.submit(form)).toBe(false); // false = preventDefault foi chamado
  });

  it("passo dos dados: method=post, preventDefault e campos de nome sem corretor nem historico", () => {
    const onContinue = vi.fn();
    const { container } = render(
      <IdentificationStep school={school} values={{}} errors={{}} headingRef={noop} onChange={noop} onBack={noop} onContinue={onContinue} />,
    );
    const form = container.querySelector("form")!;
    expect(form.method).toBe("post");
    expect(fireEvent.submit(form)).toBe(false);
    const inputs = [...container.querySelectorAll<HTMLInputElement>("input")];
    expect(inputs).toHaveLength(3);
    for (const input of inputs) {
      expect(input.getAttribute("spellcheck")).toBe("false");
      expect(input.getAttribute("autocorrect")).toBe("off");
      expect(input.getAttribute("autocapitalize")).toBe("words");
    }
    // aluno e turma: sem autopreenchimento; responsavel: so o token padrao 'name'
    expect(container.querySelector("input[name=studentName]")!.getAttribute("autocomplete")).toBe("off");
    expect(container.querySelector("input[name=classroom]")!.getAttribute("autocomplete")).toBe("off");
    expect(container.querySelector("input[name=guardianName]")!.getAttribute("autocomplete")).toBe("name");
  });

  it("login: method=post e a senha nunca iria para a URL", () => {
    const { container } = render(<LoginForm />);
    const form = container.querySelector("form")!;
    expect(form.method).toBe("post");
    expect(fireEvent.submit(form)).toBe(false);
  });

  it("guarda no codigo-fonte: todo <form> do projeto declara method=\"post\"", () => {
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.tsx$/.test(name) && !/\.test\.tsx$/.test(name)) {
          const source = readFileSync(full, "utf8");
          for (const match of source.matchAll(/<form\b[^>]*>/g)) {
            if (!/method="post"/.test(match[0])) offenders.push(`${full}: ${match[0].slice(0, 60)}`);
          }
        }
      }
    };
    walk(join(__dirname, ".."));
    expect(offenders).toEqual([]);
  });
});
