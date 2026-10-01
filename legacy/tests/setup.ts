import { randomBytes } from "node:crypto";

process.env.DATABASE_URL ??= "postgres://apm_app:apm_app@localhost:5432/apm_test";
process.env.DATABASE_ADMIN_URL ??= "postgres://apm:apm@localhost:5432/apm_test";
process.env.APP_ENCRYPTION_KEY ??= randomBytes(32).toString("base64");
