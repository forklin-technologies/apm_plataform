/** Erro de regra de negócio: mensagem segura para mostrar à família/admin. */
export class ErroNegocio extends Error {
  constructor(public codigo: string, mensagem: string, public status = 422) {
    super(mensagem);
    this.name = "ErroNegocio";
  }
}

/** Falha de comunicação com o provedor Pix (banco). */
export class ErroProvedorPix extends Error {
  constructor(mensagem: string, public httpStatus?: number, public corpo?: unknown) {
    super(mensagem);
    this.name = "ErroProvedorPix";
  }
}
