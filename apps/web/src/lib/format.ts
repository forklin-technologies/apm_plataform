/**
 * Datas no fuso de Sao Paulo e em pt-BR. O fuso e fixo para o servidor e o navegador
 * renderizarem o mesmo texto (sem divergencia de hidratacao).
 */
const TIME_ZONE = "America/Sao_Paulo";

const dateFormat = new Intl.DateTimeFormat("pt-BR", {
  timeZone: TIME_ZONE,
  day: "2-digit",
  month: "short",
});

const dateLongFormat = new Intl.DateTimeFormat("pt-BR", {
  timeZone: TIME_ZONE,
  day: "numeric",
  month: "long",
  year: "numeric",
});

const timeFormat = new Intl.DateTimeFormat("pt-BR", {
  timeZone: TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateShort(iso: string): string {
  return dateFormat.format(new Date(iso)).replace(".", "");
}

export function formatDateLong(iso: string): string {
  return dateLongFormat.format(new Date(iso));
}

export function formatTime(iso: string): string {
  return timeFormat.format(new Date(iso));
}

export function formatDateTime(iso: string): string {
  return `${formatDateLong(iso)}, ${formatTime(iso)}`;
}

/** 600000 ms -> "10:00". Nunca negativo. */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
