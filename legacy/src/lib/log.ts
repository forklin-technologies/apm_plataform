import pino from "pino";

export const log = pino({
  level: process.env.LOG_LEVEL ?? (process.env.NODE_ENV === "test" ? "silent" : "info"),
  redact: {
    paths: ["*.clientSecret", "*.client_secret", "*.senha", "*.access_token", "*.certificado", "*.pixCopiaECola",
            "req.headers.authorization", "*.responsavelEmail", "*.responsavelTelefone"],
    censor: "[oculto]",
  },
});
